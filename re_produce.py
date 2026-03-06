import json
from openai import OpenAI
import os
from pathlib import Path

import config
import re


def collect_title_groups(pdf_info, max_titles: int = 40, max_follow_blocks: int = 3):
    """
    从 pdf_info 中抽取“标题 + 后续 1~3 个块”的样本。

    优化点：
    - 如果标题数量足够多，则尽量：前半部分从前往后选，后半部分从后往前选，保证样本的多样性。
    """
    flat_blocks = []

    # 1. 拍平成一维列表，顺序与文档一致
    for page in pdf_info:
        for blk in page.get("para_blocks", []):
            text = (blk.get("text") or "").strip()
            if not text:
                continue
            # 可选：过滤过长的块（避免 prompt 太长）
            if len(text) > 200:
                continue
            flat_blocks.append({
                "type": blk.get("type"),
                "text": text,
            })

    if not flat_blocks:
        return []

    # 2. 收集所有标题的位置（索引）
    title_positions = [
        idx for idx, blk in enumerate(flat_blocks)
        if blk.get("type") == "title" and (blk.get("text") or "").strip()
    ]

    if not title_positions:
        return []

    # 3. 根据标题数量决定如何取样
    if len(title_positions) <= max_titles:
        chosen_positions = title_positions
    else:
        half = max_titles // 2
        # 先取前 half 个标题
        front_positions = title_positions[:half]

        # 再从“剩余的标题”里，从后往前取，直到凑够 max_titles
        remaining = [pos for pos in title_positions if pos not in front_positions]
        back_positions = []

        i = len(remaining) - 1
        while i >= 0 and len(front_positions) + len(back_positions) < max_titles:
            back_positions.append(remaining[i])
            i -= 1

        chosen_positions = front_positions + back_positions

    # 为了阅读友好，把最终选中的标题位置排序（从前到后）
    chosen_positions = sorted(chosen_positions)

    # 4. 根据标题位置构造样本：title + 后续 1~max_follow_blocks 个块
    title_groups = []
    for pos in chosen_positions:
        title_text = (flat_blocks[pos].get("text") or "").strip()
        if not title_text:
            continue

        context_blocks = []
        for j in range(1, max_follow_blocks + 1):
            if pos + j < len(flat_blocks):
                ctx_text = (flat_blocks[pos + j].get("text") or "").strip()
                if ctx_text:
                    context_blocks.append(ctx_text)
            else:
                break

        title_groups.append({
            "title": title_text,
            "context": context_blocks,
        })

    return title_groups


def ask_model_for_regex(title_groups, client):
    """
    让模型根据“若干功能模块标题样本”，一次性生成 若干 个不同的候选正则规则。

    约定：
    - 返回的 JSON 中 patterns 为一个数组，每个元素是一条候选 regex 方案；
    - 每条 regex 都是“尝试尽量覆盖功能模块标题”的一种可能写法；
    - 由后续的 review_regex_with_llm() 从中选出综合效果最好的一个。

    """

    print("生成规则中..")

    # 只抽取标题文本作为样本，尽量简单
    title_lines = []
    for idx, group in enumerate(title_groups, 1):
        title = (group.get("title") or "").strip()
        if title:
            title_lines.append(f"样本{idx}: {title}")

    samples_text = "\n".join(title_lines)

    user_prompt = f"""
我有一份需求文档，里边有“标题行”和“普通正文行”。
其中我们特别关注“功能模块标题”这一类标题行（通常是带有编号的功能点/用例/模块标题等）。

什么是功能模块标题：
1.一篇文档中的功能模块标题往往是同一个标题等级下的，例如如果都是第三级标题，则都是2.2节下的2.2.1、2.2.2；
2.功能模块标题往往是某一个功能的具体需求，例如说：登录、选课系统实现、扫描入库系统
3.如果标题中包括“目的”“背景”，往往不是功能模块标题。

下面是若干标题的示例（只包含标题本身）：

{samples_text}
你的任务只有一件事：
根据这些样本标题（包含功能模块标题，需自行甄别），设计 10 左右条不同的正则表达式方案（Python re 语法），
用于识别“功能模块标题”这一类行。

要求：
1. 每一条正则都是“候选方案”，尝试以不同的思路覆盖功能模块标题：
   - 有的可以略微宽松一点，以提高召回率；
   - 有的可以略微严格一点，以提高精确度；
   但都不能明显过窄（例如只匹配极少数 1～2 条样本）。
2. 所有正则都应该尽量多地覆盖这些样本中的功能模块标题，但不是尽可能多地覆盖所有标题。
3. 功能模块标题通常在同一章节内，比如说2.1.1 登录 和2.1.2 选课系统
4. 每一个正则应该对应同一章节，比如 （一.引言 二.简介）为一类，或者（1.1 xx  1.2 xx）为一类 
5. 对于每一条正则，请给出若干应该被它匹配到的示例标题（从上面的样本中选择功能模块标题）。
6. 对于每一条正则，尽量关注它的数字前缀（一、1.1），而不是关注如何匹配它的标题文本内容。
7. 对于每一条正则，当遇到标题情况：1.1.1 xx和1.1.1xx：正则应该使两种情况都能被匹配上，正则中不要增加多余的空格，正则中不要增加多余的空格，正则中不要增加多余的空格。

请你只返回一个 JSON，格式必须严格如下（注意是 JSON，不要加注释）：

{{
  "patterns": [
    {{
      "name": "简短描述这个候选规则的思路，例如：'数字层级编号+标题文本'",
      "regex": "在这里写正则，使用 Python re 语法，注意要对反斜杠进行转义，例如 \\\\d+",
      "examples_should_match": [
        "列举几条应该被这个正则匹配到的样本标题（从samples_text中选，并要求符合regex）"
      ]
    }}
  ]
}}

补充要求：
1. 只返回 JSON，不要有其他任何文字。
2. patterns 数组长度必须在 10 条左右。
3. 请确保 JSON 结构合法，便于程序解析。
4. JSON 字符串中的反斜杠要正确转义（例如 \\\\d+）。
5. examples_should_match 应与 regex 能够匹配。
6. 正则中尽量不要出现空格。
"""

    resp = client.chat.completions.create(
        model=config.MODEL,
        messages=[
            {"role": "system", "content": "你是擅长正则表达式与模式归纳的资深工程师。"},
            {"role": "user", "content": user_prompt},
        ],
        # 比较高一点的 temperature，利于生成多样化候选方案
        temperature=0.2,
    )
    return resp.choices[0].message.content

def extract_json_text(raw_json: str) -> str:
    """
    从 LLM 返回的字符串中尽可能鲁棒地提取 JSON 内容。
    尝试顺序：
    1. ```json ... ``` 代码块
    2. ``` ... ``` 普通代码块
    3. 如果包含 ```json 但没有闭合 ```，从 ```json 之后的内容当作 JSON 候选
    4. 否则直接返回整体 strip 后的内容
    """

    if not raw_json:
        return ""

    text = raw_json.strip()

    # 1) 尝试匹配 ```json ... ``` 形式（最优先）
    m = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    if m:
        return m.group(1).strip()

    # 2) 尝试匹配任意 ``` ... ``` 代码块
    m = re.search(r"```([\s\S]*?)```", text)
    if m:
        return m.group(1).strip()

    # 3) 只有 ```json 没有闭合 ``` 的情况
    idx = text.find("```json")
    if idx != -1:
        candidate = text[idx + len("```json"):]
        candidate = candidate.lstrip("\n\r ")
        # 如果末尾有多余的 ```，顺手去掉
        candidate = candidate.rstrip()
        if candidate.endswith("```"):
            candidate = candidate[:-3].rstrip()
        return candidate

    # 4) 默认：整个返回当作 JSON
    return text

def review_regex_with_llm(json_text: str, client):
    """
    评审阶段（强制选一版）：
    - 本地先做 JSON 结构校验；
    - 评审 LLM 做两件事：
      1）检查每个 pattern 内的 regex 是否大致能匹配它自己的 examples_should_match；
      2）在所有候选中“必须”选出一个相对最好的 best_pattern 返回。

    返回: (ok: bool, reason: str, best_pattern: dict | None)
      - ok=True 且 best_pattern 非空：正常通过，reason 是整体说明
      - ok=False：说明评审阶段在结构/解析上出了问题（比如 LLM 返回格式不对），此时 best_pattern 为 None
    """

    # 先做一层本地结构校验，避免连 JSON 都不是还浪费一次 LLM 调用
    try:
        data = json.loads(json_text)
    except Exception as e:
        return False, f"JSON 解析失败: {e}", None

    if not isinstance(data, dict) or "patterns" not in data:
        return False, "JSON 结构缺少 patterns 字段", None

    patterns = data.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        return False, "patterns 字段不是非空数组", None

    # 每个 pattern 至少要有 regex 字段
    for i, p in enumerate(patterns):
        if not isinstance(p, dict) or "regex" not in p:
            return False, f"patterns[{i}] 中缺少 regex 字段", None

    review_model = getattr(config, "REVIEW_MODEL", config.MODEL)

    review_prompt = f"""
下面是候选正则配置 JSON（patterns 为若干候选规则）：

============ 生成的 JSON 开始 ============
{json_text}
============ 生成的 JSON 结束 ============
什么是功能模块标题：
1.一篇文档中的功能模块标题往往是同一个标题等级下的，例如如果都是第三级标题，则都是2.2节下的2.2.1、2.2.2；
2.功能模块标题往往是某一个功能的具体需求，例如说：登录、选课系统实现、扫描入库系统
3.如果标题中包括“目的”“背景”，往往不是功能模块标题。

请你做一件事：

在所有候选中选出一个“相对最优”的规则
------------------------------------------
你必须在 patterns 中选出一个 best_pattern，不能全部否定。
选择时的考虑顺序：
1. 这个 regex 与它的 examples_should_match 中的功能模块标题 大致自洽,如果 examples_should_match 中大部分不是功能模块标题则跳过；
2. 它的形式看起来比较符合“功能模块标题”的风格（带编号的结构、章节内功能点等），而不是普通正文；
3. 在多个看起来都合理的候选中，选一个你最推荐的。

注意：即使所有候选都有缺点，也要选出相对最不差的一个。

输出格式必须是下面这个 JSON（不要有其他任何文字）：

{{
  "ok": true,
  "overall_reason": "简要说明你为什么选中这条（中文）",
  "best_pattern_index": 整数索引,  // 0-based，对应 patterns 数组下标
  "best_pattern": {{
    "name": "从原 JSON 中拷贝的 name（如果原来有）",
    "regex": "从原 JSON 中拷贝的 regex",
    "examples_should_match": [
      "从原 JSON 中拷贝的 examples_should_match（可原样返回，不必修改）"
    ]
  }}
}}

要求：
- ok 必须为 true；
- best_pattern_index 必须为合法下标；
- best_pattern 必须从原 JSON 的 patterns 对应元素拷贝而来（字段可少，但 regex 必须保留）。
"""

    try:
        resp = client.chat.completions.create(
            model=review_model,
            messages=[
                {"role": "system", "content": "你是一个只关心 regex 是否能大致匹配功能模块标题的严格评审，并且必须从候选中选出一个最优方案。"},
                {"role": "user", "content": review_prompt},
            ],
            temperature=0.0,
        )
        raw_review = resp.choices[0].message.content or ""
    except Exception as e:
        return False, f"调用评审 LLM 失败: {e}", None

    # 利用前面的 extract_json_text 先剥掉可能的 ```json 包裹
    review_json_text = extract_json_text(raw_review)

    try:
        review_data = json.loads(review_json_text)
    except Exception as e:
        return False, f"评审结果 JSON 解析失败: {e}，原始内容: {review_json_text!r}", None

    ok = bool(review_data.get("ok"))
    overall_reason = str(review_data.get("overall_reason", "（无详细说明）"))
    best_pattern = review_data.get("best_pattern")
    best_idx = review_data.get("best_pattern_index")

    # 这里仍然做一点保护校验：防止评审 LLM 格式写错
    if not ok:
        return False, f"评审结果中 ok 不为 true：{overall_reason}", None

    if not isinstance(best_pattern, dict) or "regex" not in best_pattern:
        return False, "评审结果中缺少有效的 best_pattern.regex 字段", None

    if not isinstance(best_idx, int):
        return False, "评审结果中 best_pattern_index 不是整数", None

    if not (0 <= best_idx < len(patterns)):
        return False, f"评审结果中的 best_pattern_index 越界：{best_idx}", None

    return True, overall_reason, best_pattern


def run(Json_Object_Path, File_Path):
    data = json.loads(Path(Json_Object_Path).read_text(encoding="utf-8"))
    pdf_info = data["pdf_info"]

    # 1. 前后各取一部分标题，收集标题样本
    title_groups = collect_title_groups(pdf_info, max_titles=80, max_follow_blocks=3)

    if not title_groups:
        print("未找到任何标题样本，无法生成正则。")
        return

    client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)

    print("\n==== 开始生成正则候选 ====")
    # 2. 调用大模型，让它根据这些 title 样本生成【多个】正则候选
    raw_json = ask_model_for_regex(title_groups, client)

    # 3. 从模型输出中尽量鲁棒地提取 JSON 文本
    json_text = extract_json_text(raw_json)

    print("\n==== 开始评审并从候选中选出最佳规则 ====")
    # 4. 让“评审 LLM”检查这份 JSON / patterns 列表，选出一个最佳 regex
    ok, reason, best_pattern = review_regex_with_llm(json_text, client)
    print(f"评审结果: ok={ok}, reason={reason}")

    if not ok or not best_pattern:
        print(f"评审阶段失败，未能选出有效规则，本次未生成正则文件。原因：{reason}")
        return

    # 5. 通过评审，保存“最佳规则”并退出
    output_path = os.path.join(File_Path, "re_json.json")

    final_data = {
        "patterns": [
            best_pattern
        ]
    }
    final_json_text = json.dumps(final_data, ensure_ascii=False, indent=2)
    Path(output_path).write_text(final_json_text, encoding="utf-8")

    print(f"最佳 Re 规则已保存到 {Path(output_path).resolve()}")

