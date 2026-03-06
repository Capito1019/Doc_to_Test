import os
import json
from pathlib import Path

import config
import doc_to_json
import json_process_simplier
import json_process_images
import re_produce
import test_produce

#doc_to_json 配置
API_TOKEN = config.API_TOKEN
MINERU_BASE_URL = config.MINERU_BASE_URL
LOCAL_FILE_PATH = r"芯片WMS管理系统需求文档2.0.docx"  # 本地文件路径
DOC_TO_JSON_OUTPUT_DIR = r"json_output"   # JSON 输出目录

#状态存储工具函数
def load_status(file_path_dir: Path) -> dict:
    """从 file_path 目录下读取进度状态，不存在则返回空 dict。"""
    status_path = file_path_dir / "_pipeline_status.json"
    if status_path.exists():
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            # 读失败就当成没有
            return {}
    return {}


def save_status(file_path_dir: Path, status: dict):
    """把进度状态写回 file_path 目录。"""
    status_path = file_path_dir / "_pipeline_status.json"
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2),
                           encoding="utf-8")

#主函数
def main():
    # 1. 调 MinerU 生成 json
    print("step1 文档转化为json文件")
    File_Path = doc_to_json.run(API_TOKEN, LOCAL_FILE_PATH, MINERU_BASE_URL, DOC_TO_JSON_OUTPUT_DIR)
    # 确保是 Path 对象
    file_path_dir = Path(File_Path)

    # 读取 / 初始化进度状态
    status = load_status(file_path_dir)

    # 先把 doc_to_json 这一步也记一下（这一步本身是否跳过由 doc_to_json.run 内部控制）
    if not status.get("doc_to_json_done"):
        status["doc_to_json_done"] = True
        save_status(file_path_dir, status)

    # 准备好后续步骤会用到的路径
    Json_Process_Input_Path = os.path.join(File_Path, "layout.json")
    Json_Process_Output_Path = Path(Json_Process_Input_Path).with_name(
        Path(Json_Process_Input_Path).stem + "_simplified.json"
    )
    Json_Object_Path = Json_Process_Output_Path
    Json_Re_Path = os.path.join(File_Path, "re_json.json")

    # 2. json_process_simplier.py 简化 json 文件
    print("step2 json文件结构简化")
    if status.get("json_simplified"):
        print("[跳过] json_simplier：已记录完成。")
    else:
        json_process_simplier.run(Json_Process_Input_Path, Json_Process_Output_Path)
        status["json_simplified"] = True
        save_status(file_path_dir, status)
        print("[完成] json_simplier：已生成简化 json。")

    # 3. json_process_images.py 对 json 文件中的图片进行 OSS 处理
    print("step3 json文件图片OSS处理")
    if status.get("images_processed"):
        print("[跳过] json_process_images：已记录完成。")
    else:
        json_process_images.run(File_Path)
        status["images_processed"] = True
        save_status(file_path_dir, status)
        print("[完成] json_process_images：图片已处理。")

    # 4. LLM 生成 Re 规则，提取文档中可能功能模块
    print("step4 LLM生成 Re 标题提取规则")
    if status.get("re_produced"):
        print("[跳过] re_produce：已记录完成。")
    else:
        re_produce.run(Json_Object_Path, File_Path)
        status["re_produced"] = True
        save_status(file_path_dir, status)
        print("[完成] re_produce：规则已生成。")

    # 5. LLM 生成功能模块测试用例
    print("step5 LLM生成功能模块测试用例")
    if status.get("testcases_generated"):
        print("[跳过] test_produce：已记录完成。")
    else:
        test_produce.run(Json_Object_Path, File_Path, Json_Re_Path)
        status["testcases_generated"] = True
        save_status(file_path_dir, status)
        print("[完成] test_produce：测试用例已生成。")

    print("\n流水线执行完毕（部分步骤可能已被跳过）。")


if __name__ == "__main__":
    main()