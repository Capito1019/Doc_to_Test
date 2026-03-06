import os
import json
import oss2
import config

from typing import Dict, List, Optional

# ========= 1. 初始化 bucket =========
def get_bucket():
    auth = oss2.Auth(config.OSS_ACCESS_KEY_ID, config.OSS_ACCESS_KEY_SECRET)
    # endpoint 不要带 bucket 名，只是类似 oss-cn-hangzhou.aliyuncs.com
    bucket = oss2.Bucket(auth, config.OSS_ENDPOINT, config.OSS_BUCKET_NAME)
    return bucket


# ========= 2. 上传单张图片，并返回公网 URL =========
def upload_image_and_get_url(bucket, local_path: str, rel_key: str) -> str:
    """
    :param bucket: oss2.Bucket 实例
    :param local_path: 本地图片路径
    :param rel_key: 希望在 OSS 中的相对路径，如 "abc123/image/page_1.png"
    :return: 公网 URL
    """
    # 构造完整的 object key，加前缀（可选）
    object_key = f"{config.OSS_OBJECT_PREFIX.rstrip('/')}/{rel_key.lstrip('/')}"
    object_key = object_key.replace("\\", "/")

    with open(local_path, "rb") as f:
        # put_object 上传文件内容
        bucket.put_object(object_key, f)

    # 拼 OSS 上的公网访问 URL（前提：bucket为公共读 / 有CDN域名）
    public_url = f"{config.OSS_PUBLIC_DOMAIN.rstrip('/')}/{object_key.lstrip('/')}"
    return public_url

# ================== 3. 辅助：根据 image_paths 里的名字找到本地文件 ==================

def find_image_file(doc_dir: str, img_name: str) -> Optional[str]:
    """
    根据 layout.json 里的 image_paths 条目找到本地真实图片路径。

    尝试顺序：
        1. doc_dir/img_name
        2. doc_dir/image/img_name
        3. doc_dir/images/img_name
    找不到就返回 None
    """
    candidates = [
        os.path.join(doc_dir, img_name),
        os.path.join(doc_dir, "image", img_name),
        os.path.join(doc_dir, "images", img_name),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


# ================== 4. 处理单个 layout.json 文件 ==================

def process_one_doc_dir(bucket: oss2.Bucket, doc_dir: str):
    layout_path = os.path.join(doc_dir, "layout_simplified.json")
    if not os.path.isfile(layout_path):
        print(f"[SKIP] {doc_dir} 中没有 layout_simplified.json，跳过")
        return

    # 读取 JSON
    with open(layout_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    pdf_info: List[dict] = data.get("pdf_info", [])
    if not isinstance(pdf_info, list):
        print(f"[WARN] {layout_path} 中 pdf_info 结构异常，跳过")
        return

    print(f"\n===== 处理文档目录: {doc_dir} =====")

    # 遍历所有 page / para_blocks，找到 type == "image"
    for page in pdf_info:
        para_blocks = page.get("para_blocks", [])
        if not isinstance(para_blocks, list):
            continue

        for blk in para_blocks:
            if not isinstance(blk, dict):
                continue

            if blk.get("type") != "image":
                continue

            img_list = blk.get("image_paths")
            if not isinstance(img_list, list):
                continue

            new_img_paths = []
            for img_name in img_list:
                if not isinstance(img_name, str):
                    new_img_paths.append(img_name)
                    continue

                # 找到本地图片绝对路径
                local_img = find_image_file(doc_dir, img_name)
                if not local_img:
                    print(f"  [WARN] 找不到图片文件: {img_name} (doc_dir={doc_dir})，保持原值")
                    new_img_paths.append(img_name)
                    continue

                # 以 doc_dir 为基准计算相对路径，避免不同文档重复文件名冲突
                rel_from_output = os.path.relpath(local_img, doc_dir).replace("\\", "/")

                # 上传并获取 URL
                url = upload_image_and_get_url(bucket, local_img, rel_from_output)
                print(f"  上传: {local_img} -> {url}")

                # 用 URL 替换原来的本地路径
                new_img_paths.append(url)

            # 用新的 URL 列表覆盖原有 image_paths
            blk["image_paths"] = new_img_paths

    # 写回 JSON，覆盖原文件
    with open(layout_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[DONE] 已更新 {layout_path} 中所有 image_paths 为 OSS URL")


def run(DOC_DIR):
    if not os.path.isdir(DOC_DIR):
        raise NotADirectoryError(f"DOC_DIR 不存在或不是目录: {DOC_DIR}")

    bucket = get_bucket()
    process_one_doc_dir(bucket, DOC_DIR)

