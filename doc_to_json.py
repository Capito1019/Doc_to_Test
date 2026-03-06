import os
import io
from pathlib import Path
import time
import zipfile
import requests
from urllib.parse import urlparse, unquote

def request_upload_url_for_single_file(local_path: str, BASE_URL: str, API_TOKEN: str):
    """
    调用 /file-urls/batch 为一个本地文件申请上传 URL
    返回 (batch_id, file_url)
    """
    file_name = os.path.basename(local_path)

    url = f"{BASE_URL}/file-urls/batch"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_TOKEN}",
    }

    payload = {
        # 这里是一些解析参数，可按需求调整，参考官网文档
        "enable_formula": True,
        "enable_table": True,
        "language": "auto",
        "layout_model": "doclayout_yolo",
        "files": [
            {
                "name": file_name,
                "data_id": "file_1",  # 你自定义的标识，后面查结果会用到
                "is_ocr": True,
            }
        ],
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"申请上传 URL 失败: {data}")

    batch_id = data["data"]["batch_id"]
    file_urls = data["data"]["file_urls"]
    if not file_urls:
        raise RuntimeError("申请成功但 file_urls 为空")

    upload_url = file_urls[0]  # 我们这里只有一个文件
    print(f"[request_upload_url] batch_id = {batch_id}")
    print(f"[request_upload_url] upload_url = {upload_url[:80]}...")
    return batch_id, upload_url


def upload_file_to_url(local_path: str, upload_url: str):
    """
    用 PUT 上传二进制文件到 MinerU 提供的临时 URL
    注意：不用设置 Content-Type，官方文档就是裸数据上传
    """
    with open(local_path, "rb") as f:
        resp = requests.put(upload_url, data=f, timeout=300)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"上传失败，状态码: {resp.status_code}, 内容: {resp.text}")
    print("[upload_file_to_url] 上传成功")


def wait_batch_result_and_get_zip_url(BASE_URL: str, API_TOKEN: str, batch_id: str, data_id: str, max_retries=120, interval=5):
    """
    调用 GET /extract-results/batch/{batch_id} 轮询解析结果，
    找到对应 data_id 的条目，返回 full_zip_url
    """
    url = f"{BASE_URL}/extract-results/batch/{batch_id}"
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }

    for i in range(max_retries):
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(f"查询批次结果失败: {result}")

        data = result["data"]
        extract_list = data.get("extract_result", [])

        # 找到我们这一个 data_id 对应的任务
        target = None
        for item in extract_list:
            if item.get("data_id") == data_id:
                target = item
                break

        if not target:
            print(f"[wait_batch_result] 第 {i+1} 次查询，仍未找到 data_id={data_id}")
        else:
            state = target.get("state")
            print(f"[wait_batch_result] 第 {i+1} 次查询，state={state}")

            if state == "done":
                zip_url = target.get("full_zip_url")
                if not zip_url:
                    raise RuntimeError("任务完成但 full_zip_url 为空")
                print(f"[wait_batch_result] 任务完成，zip_url = {zip_url[:80]}...")
                return zip_url
            elif state == "failed":
                raise RuntimeError(f"解析失败: {target.get('err_msg')}")

        time.sleep(interval)

    raise TimeoutError("轮询超时，任务仍未完成")


def _make_zip_output_dir(base_output_dir: str, source_filename: str):
    """
    根据源文档名生成子目录名，例如：
    /path/to/xxx.docx -> <base_output_dir>/xxx
    """
    os.makedirs(base_output_dir, exist_ok=True)

    # 提取不带扩展名的主文件名
    basename = os.path.basename(source_filename)
    name_no_ext = os.path.splitext(basename)[0]

    subdir_name = name_no_ext
    out_dir = os.path.join(base_output_dir, subdir_name)
    os.makedirs(out_dir, exist_ok=True)

    return out_dir, subdir_name + ".zip"   # zip 文件名仍然要有扩展名


def download_save_zip_layout_and_images(zip_url: str, base_output_dir: str, source_filename: str):
    """
    下载 zip → 保存 zip 文件 → 仅解压：
      - layout.json
      - image 文件夹（及其中的内容）

    子目录名称基于 source_filename
    """
    # 生成子目录
    zip_output_dir, zip_filename = _make_zip_output_dir(base_output_dir, source_filename)

    print(f"[download_save_zip_layout_and_images] 下载 ZIP: {zip_url}")
    resp = requests.get(zip_url, timeout=300)
    resp.raise_for_status()

    # 保存 zip
    zip_path = os.path.join(zip_output_dir, zip_filename)
    with open(zip_path, "wb") as f:
        f.write(resp.content)
    print(f"[download_save_zip_layout_and_images] ZIP 已保存: {zip_path}")

    layout_path = None
    image_files = []

    # 解压 layout.json 和 image 文件夹
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for member in zf.namelist():
            lower = member.lower()

            if lower.endswith("layout.json"):
                target_path = os.path.join(zip_output_dir, "layout.json")
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())
                layout_path = target_path
                print(f"[download_save_zip_layout_and_images] layout.json 已保存: {target_path}")
                continue

            if lower.startswith("image/") or lower.startswith("images/"):
                target_path = os.path.join(zip_output_dir, member.replace("\\", "/"))
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())
                image_files.append(target_path)
                print(f"[download_save_zip_layout_and_images] image 文件已保存: {target_path}")

    return zip_path, layout_path, image_files, zip_output_dir

def run(API_TOKEN: str, LOCAL_FILE_PATH: str, BASE_URL: str, OUTPUT_DIR: str):
    if not API_TOKEN:
        raise ValueError("请设置 MinerU API_TOKEN")

    if not os.path.isfile(LOCAL_FILE_PATH):
        raise FileNotFoundError(f"本地文件不存在: {LOCAL_FILE_PATH}")

    # ---------------------------
    # 1) 计算目标输出目录（根据文档名）
    # ---------------------------
    # OUTPUT_DIR / <doc_name_without_ext>
    OUTPUT_DIR = Path(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(exist_ok=True)

    doc_name = Path(LOCAL_FILE_PATH).stem
    target_dir = OUTPUT_DIR / doc_name

    # ---------------------------
    # 2) 如果目录已经存在 → 跳过解析
    # ---------------------------
    if target_dir.exists():
        print(f"[run] 目录已存在，跳过 MinerU 解析：{target_dir}")
        return str(target_dir)

    # ---------------------------
    # 3) 目录不存在 → 正常解析流程
    # ---------------------------
    batch_id, upload_url = request_upload_url_for_single_file(
        LOCAL_FILE_PATH, BASE_URL, API_TOKEN
    )
    upload_file_to_url(LOCAL_FILE_PATH, upload_url)

    zip_url = wait_batch_result_and_get_zip_url(
        BASE_URL, API_TOKEN, batch_id, data_id="file_1"
    )

    # 注意：此时 download_save_zip_layout_and_images 要支持根据文档名创建目标目录
    zip_path, layout_path, image_files, zip_output_dir = download_save_zip_layout_and_images(
        zip_url=zip_url,
        base_output_dir=str(OUTPUT_DIR),
        source_filename=LOCAL_FILE_PATH   # 这行你要确保已按前面回答那样修改过
    )

    print("\n===== 完成 =====")
    return zip_output_dir
