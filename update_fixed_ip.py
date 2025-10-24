# update_fixed_ip.py
import requests
import os

ZBY_FILE = "zby.txt"
FIXED_API = "https://raw.githubusercontent.com/xiaolin330328/ctv/refs/heads/main/第二"

def update_zby():
    # 读取原文件内容
    if os.path.exists(ZBY_FILE):
        with open(ZBY_FILE, "r", encoding="utf-8") as f:
            original_content = f.read()
    else:
        original_content = ""

    try:
        resp = requests.get(FIXED_API, timeout=20)
        resp.raise_for_status()
        api_content = resp.text.strip()
    except Exception as e:
        print(f"⚠ 固定接口抓取失败: {e}")
        return

    # 拼接新增部分
    new_content = "\n\n# ===== 新增直播源 =====\n\n" + api_content + "\n"

    # 去掉旧的新增部分
    split_marker = "# ===== 新增直播源 ====="
    if split_marker in original_content:
        original_content = original_content.split(split_marker)[0].rstrip()

    # 写回文件
    with open(ZBY_FILE, "w", encoding="utf-8") as f:
        f.write(original_content + "\n" + new_content.strip())

    print(f"✅ zby.txt 已更新，新增直播源来自固定接口。")

if __name__ == "__main__":
    update_zby()
