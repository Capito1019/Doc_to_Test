import json
import re
from pathlib import Path

from html import unescape

try:
    from bs4 import BeautifulSoup  # pip install beautifulsoup4
except ImportError:
    BeautifulSoup = None

from openai import OpenAI
import os
import config


def table_html_to_text(html: str) -> str:
    """
    把 table 的 HTML 转成简单的文本表格，便于大模型理解。
    格式示例：
    字段 | 类型 | 是否必填
    仓库编码 | varchar(20) | 是
    ...
    """
    if not BeautifulSoup:
        # 没装 bs4 时，退化为直接返回原始 html
        return html

    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [cell.get_text(strip=True) for cell in tr.find_all(["td", "th"])]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def module_to_plain_text(module: dict) -> str:
    """
    把一个模块（包括 text / table / list / image 等块）转成纯文本，给大模型用。
    module 的结构来自你前面构造的 modules 列表。
    """
    pieces = []
    # 模块抬头
    pieces.append(f"模块标题：{module['title']}")
    pieces.append("")  # 空行

    for blk in module["blocks"]:
        btype = blk.get("type")

        # 1) 普通文字
        if btype == "text":
            text = (blk.get("text") or "").strip()
            if text:
                pieces.append(text)

        # 2) 列表
        elif btype == "list":
            items = blk.get("items") or []
            if items:
                pieces.append("\n".join(f"- {item}" for item in items))

        # 3) 表格
        elif btype == "table":
            html = blk.get("html") or ""
            if html:
                pieces.append("[表格说明]")
                pieces.append(table_html_to_text(html))

        # 4) 图片（界面、流程图等）
        elif btype == "image":
            captions = blk.get("captions") or []
            if captions:
                pieces.append("[图片/界面说明] " + "；".join(captions))
            else:
                pieces.append("[图片/界面]")

        # 其他类型，按需补充
        else:
            # 如果块里也有 text，可以兜底用一下
            text = (blk.get("text") or "").strip()
            if text:
                pieces.append(text)

    return "\n".join(pieces)

def ask_model_is_function_module(title: str, blocks: list, client) -> bool:
    """
    让 LLM 判断某个“章节（标题 + 内容）”是不是【具体功能模块】。

    判定标准（通过 prompt 告知 LLM）：
    - 只有类似“登录”“选课”“提交订单”“查看成绩”这类
      可以看成一个完整、可执行的功能点/用例/操作流程，才算 YES。
    - 各种“引言、总体说明、系统概述、业务背景、术语说明、非功能需求、
      部署说明、数据字典、接口列表汇总”等，一律 NO。
    """

    text = module_to_plain_text({"title": title, "blocks": blocks})

    prompt = f"""
下面是文档中的一个“章节标题 + 章节内容”：请你判断它是不是一个【具体的功能模块】。

\"\"\"章节内容开始
{text}
章节内容结束\"\"\"


【功能模块】在这里的定义非常严格，只在以下情况下判定为 YES：
- 这个章节主要描述一个“可以单独拿出来作为功能点/用例的具体功能”，例如：
  - “用户登录”“找回密码”“用户注册”
  - “选课”“退课”“提交订单”“取消订单”
  - “查看成绩”“导出报表”“上传附件”
- 这个章节围绕某个具体操作/流程展开，通常包含：
  - 参与角色、前置条件
  - 操作步骤或交互流程
  - 输入输出、界面字段、按钮行为等

以下情况都不能算功能模块（必须判定为 NO）：
- 章节内容是概论、引言、背景、名词解释：
  - 如“1 引言”“1.1 编写目的”“1.2 预期读者”“术语和缩略语说明”
- 章节内容是整体系统或业务的高层描述：
  - 如“系统总体架构”“业务流程概述”“系统边界”“设计原则”
- 章节内容是通用的非功能性需求：
  - 如“性能要求”“安全性要求”“可靠性要求”“可用性要求”
- 章节内容是环境/部署/运维：
  - 如“运行环境”“部署方案”“安装步骤”“备份恢复策略”
- 章节内容是纯数据/字段/表结构说明，但不围绕一个具体功能场景：
  - 如“数据字典”“基础数据编码”“字段说明”
- 章节内容是跨多个功能的汇总性章节：
  - 如“功能列表总览”“接口列表”“菜单结构说明”

判断策略：
- 更看重“章节标题”和“内容所描述的核心对象”是不是一个可以当作功能点/用例的“动作 + 目标”；
- 如果标题比较抽象，但内容明显是围绕某一个具体操作流程（带步骤、按钮、输入输出）展开，也可以判为 YES；
- 如果无法确定是否是具体功能模块，请倾向于判定为 NO。

请根据以上标准给出结论：
- 如果你认为这是一个【具体功能模块】，请只输出：YES
- 否则，请只输出：NO

注意：务必只输出 YES 或 NO，不要输出任何其他文字。
"""

    resp = client.chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return resp.choices[0].message.content.strip() == "YES"

def gen_testcases_for_module(module: dict, idx: int, client) -> str:
    """
    返回：大模型生成的测试用例文本
    你也可以让模型按 JSON 格式输出，方便后面再处理成 xlsx / testlink 导入等。
    """
    requirement_text = module_to_plain_text(module)

    prompt = f"""
现在要根据功能需求，为功能模块「{module['title']}」设计测试用例。

要求：
1. 输出为**表格文本**（用 | 分隔列），字段包括：
   - 用例编号
   - 用例标题
   - 前置条件
   - 测试步骤
   - 预期结果
2. 覆盖正常流程、异常输入、边界条件等。
3. 用例编号建议以模块编号为前缀，例如 {idx}-001, {idx}-002 ...

以下是该模块的需求说明原文（包括功能描述、输入、操作流程、字段说明等）：

\"\"\" 
{requirement_text}
\"\"\"

请直接输出用例表格，不要加多余说明。
"""

    resp = client.chat.completions.create(
        model=config.MODEL,  # 或你实际使用的模型
        messages=[
            {"role": "system", "content": "你是专业的软件测试工程师，擅长根据需求设计测试用例。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content

def run(Json_Object_Path, File_Path, Json_Re_Path):
    # 1. 读取 json
    data = json.loads(Path(Json_Object_Path).read_text(encoding="utf-8"))

    pdf_info = data["pdf_info"]

    result = json.loads(Path(Json_Re_Path).read_text(encoding="utf-8"))
    patterns = result["patterns"]

    compiled = []
    for p in patterns:
        name = p["name"]
        regex = p["regex"]
        try:
            r = re.compile(regex)
        except re.error as e:
            print(f"正则编译失败: {name} / {regex} / error={e}")
            continue
        compiled.append((name, r))

    # 假设只有一个“功能模块标题”规则
    module_pattern = compiled[0][1]

    modules = []
    current = None

    for page in pdf_info:
        page_idx = page.get("page_idx")
        for blk in page.get("para_blocks", []):
            # 先拿 text，可能为空，用来做“是不是标题”的判断
            text = (blk.get("text") or "").strip()

            # 判断是否为模块标题：有文本 且 能被正则匹配到
            is_module_title = bool(text and module_pattern.match(text))

            if is_module_title:
                # 遇到新模块标题
                if current is not None:
                    modules.append(current)

                current = {
                    "title": text,
                    "start_page": page_idx,
                    "end_page": page_idx,
                    "blocks": []
                }
            else:
                # 不是标题（或者没有 text），但如果已经在某个模块里，就把所有类型的块都塞进去
                if current is not None:
                    current["blocks"].append(
                        {
                            "page_idx": page_idx,
                            **blk,  # 保留原来的 type / text / html / items / captions 等所有信息
                        }
                    )
                    current["end_page"] = page_idx

    # 别忘了最后一个模块
    if current is not None:
        modules.append(current)

    print(f"自动识别模块 {len(modules)} 个")
    for m in modules:
        print(m["title"], "pages:", m["start_page"], "->", m["end_page"])

    
    client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)

    # 生成所有模块的测试用例
    all_results = {}

    idx = 0
    for m in modules:
        print(f"正在为模块 {idx} {m['title']} 生成测试用例...")

        if not ask_model_is_function_module(m['title'], m['blocks'], client):
            print(f"跳过非功能模块：{m['title']}")
            continue

        cases_text = gen_testcases_for_module(m, idx, client)
        all_results[f"{idx} {m['title']}"] = cases_text
        idx += 1

    # 示例：把每个模块的用例保存成单独的 .md 文件
    out_dir = Path(File_Path) / "testcases"
    out_dir.mkdir(parents=True, exist_ok=True)
    for key, text in all_results.items():
        safe_name = key.replace(" ", "_").replace("/", "_")
        (out_dir / f"{safe_name}.md").write_text(text, encoding="utf-8")

    

    
    