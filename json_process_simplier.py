import json
from pathlib import Path

def extract_text_from_lines(lines):
    """
    从 block['lines'] 结构中，把所有 span.type == 'text' 的 content 拼起来。
    去掉 bbox 等排版信息，只保留纯文本。
    """
    texts = []
    if not lines:
        return ""

    for line in lines:
        for span in line.get("spans", []):
            if span.get("type") == "text" and span.get("content"):
                texts.append(span["content"])
    # 这里按需要选择用 "" 连接还是加空格 / 换行
    return "".join(texts).strip()

def simplify_block(block: dict):
    """
    针对 para_blocks 里的一个 block，去掉排版元素，只保留有用信息。
    - text/title: 提取纯文本为 block['text']
    - list:       提取内部每一项文字为 items: [item1, item2, ...]
    - table:      尝试保留 html
    - image:      保留 image_path（可以是多个）以及文字说明（caption）
    其他排版字段（bbox, angle, index 等）全部丢弃。
    """
    btype = block.get("type")
    simple = {"type": btype}

    # 普通文本 / 标题
    if btype in {"text", "title"}:
        text = extract_text_from_lines(block.get("lines", []))
        if text:
            simple["text"] = text

    # 列表：block['blocks'] 里是多个小 block
    elif btype == "list":
        items = []
        for item in block.get("blocks", []):
            if item.get("type") in {"text", "title"}:
                t = extract_text_from_lines(item.get("lines", []))
                if t:
                    items.append(t)
        if items:
            simple["items"] = items

    # 表格：保留 html（如果有）
    elif btype == "table":
        html = None
        for cellblock in block.get("blocks", []):
            for line in cellblock.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("type") == "table" and span.get("html"):
                        html = span["html"]
                        break
                if html:
                    break
            if html:
                break
        if html:
            simple["html"] = html

    # ⭐ 图片：保留 image_path & caption 文本
    elif btype == "image":
        image_paths = []
        captions = []

        for sub in block.get("blocks", []):
            # image_body / image_caption 都在这里
            for line in sub.get("lines", []):
                for span in line.get("spans", []):
                    stype = span.get("type")
                    if stype == "image" and span.get("image_path"):
                        image_paths.append(span["image_path"])
                    elif stype == "text" and span.get("content"):
                        captions.append(span["content"])

        if image_paths:
            # 如果有多张图，就保留一个列表
            simple["image_paths"] = image_paths
        if captions:
            # caption 可能有多段，这里用列表保存
            simple["captions"] = captions

    # 如果除了 type 之外没有任何有效内容，就返回 None（表示丢掉这个 block）
    if len(simple) == 1:
        return None

    return simple

def simplify_page(page: dict):
    """
    输入一页的结构（原始 page），输出精简后的结构：
    {
        "page_idx": ...,
        "para_blocks": [ 简化后的 block1, block2, ... ]
    }
    """
    new_blocks = []
    for b in page.get("para_blocks", []):
        sb = simplify_block(b)
        if sb is not None:
            new_blocks.append(sb)

    return {
        "page_idx": page.get("page_idx"),
        "para_blocks": new_blocks
    }

def run(INPUT_PATH, OUTPUT_PATH):
    with Path(INPUT_PATH).open("r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # 原始数据中，按你这个文件结构是 raw_data["pdf_info"] 是页列表
    pages = raw_data["pdf_info"]
    simplified_pages = [simplify_page(p) for p in pages]

    output_data = {
        "pdf_info": simplified_pages
    }

    with Path(OUTPUT_PATH).open("w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print("已保存精简后的文件到：", OUTPUT_PATH)

