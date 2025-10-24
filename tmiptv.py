import requests
import re
import time
import os
import concurrent.futures
from urllib.parse import urljoin, urlparse, urlunparse, quote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ======================
# 增强版配置参数
# ======================
MAX_WORKERS = 15  # 并发数
SPEED_THRESHOLD = 0.15  # 原有测速阈值 KB/s
REQUEST_TIMEOUT = 20  # 超时时间
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
RETRY_STRATEGY = Retry(
    total=5,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)

class IPTVUpdater:
    def __init__(self):
        self.channel_dict = {}  # 字典去重
        self.fixed_groups = {}  # 固定接口分组字典
        self.session = self._create_session()
        self.sources = [
            "https://d.kstore.dev/download/10694/zmtvid.txt",
            "https://raw.githubusercontent.com/iptv-org/iptv/master/scripts/sources.md",
            "https://raw.githubusercontent.com/freeiptv/iptv/master/sources.md"
        ]

    def _create_session(self):
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=RETRY_STRATEGY)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({
            'User-Agent': USER_AGENT,
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })
        return session

    def _standardize_url(self, raw_url):
        try:
            if not raw_url.startswith(('http://', 'https://')):
                raw_url = f'http://{raw_url.strip("/")}'
            parsed = urlparse(raw_url)
            if parsed.path.endswith('.m3u') or parsed.path.endswith('.txt'):
                return raw_url
            return urlunparse((
                parsed.scheme,
                parsed.netloc,
                '/iptv/live/1000.json',
                '',
                'key=txiptv',
                ''
            ))
        except Exception as e:
            print(f"URL标准化失败: {raw_url} - {str(e)}")
            return None

    def _fetch_sources(self):
        unique_urls = set()
        for source in self.sources:
            try:
                print(f"\n▷ 正在抓取源: {source}")
                response = self.session.get(source, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                url_pattern = r"(?:https?:\/\/)?(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)"
                matches = re.findall(url_pattern, response.text)
                print(f"发现原始URL数量: {len(matches)}")
                for url in matches:
                    if any(x in url for x in ['github.com', 'raw.githubusercontent.com']):
                        continue
                    std_url = self._standardize_url(url)
                    if std_url:
                        unique_urls.add(std_url)
            except Exception as e:
                print(f"⚠ 源抓取失败: {source} - {str(e)}")
        return list(unique_urls)

    def _speed_test(self, url):
        try:
            start_time = time.time()
            with self.session.get(url, stream=True, timeout=(5, 15)) as response:
                response.raise_for_status()
                target_size = 1024 * 1024
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    downloaded += len(chunk)
                    if downloaded >= target_size or time.time() - start_time > 20:
                        break
                duration = max(time.time() - start_time, 0.1)
                speed = (downloaded / 1024) / duration
                return round(speed, 2)
        except Exception as e:
            print(f"⛔ 测速失败 {url}: {str(e)}")
            return 0

    def _process_api(self, api_url):
        print(f"\n▶ 正在处理API端点: {api_url}")
        try:
            response = self.session.get(api_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            try:
                data = response.json()
                if not isinstance(data.get('data'), list):
                    print(f"⚠ 无效数据结构: {api_url}")
                    return
            except ValueError:
                print(f"⚠ JSON解析失败: {api_url}")
                return
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = []
                for idx, channel in enumerate(data['data'][:200]):
                    if not all(k in channel for k in ('name', 'url')):
                        continue
                    try:
                        full_url = urljoin(api_url, channel['url'].strip())
                        unique_key = f"{channel['name'].strip()}|{full_url}"
                        if unique_key not in self.channel_dict:
                            futures.append((channel['name'].strip(), full_url, executor.submit(self._speed_test, full_url)))
                    except Exception as e:
                        print(f"⚠ 频道处理异常: {str(e)}")
                for name, url, future in futures:
                    speed = future.result()
                    if speed > SPEED_THRESHOLD:
                        self.channel_dict[f"{name}|{url}"] = f"{name},{url}"
                        print(f"✔ {name.ljust(20)} {speed} KB/s")
                    else:
                        print(f"✘ {name.ljust(20)} 速度不足 {speed} KB/s")
        except Exception as e:
            print(f"⚠ 请求失败: {str(e)}")

    def _process_fixed_api(self, api_url):
        """固定接口拉取直播源，保留原始分组"""
        try:
            parsed = urlparse(api_url)
            path = quote(parsed.path)
            fixed_url = urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))

            print(f"\n▶ 正在处理固定接口: {fixed_url}")
            response = self.session.get(fixed_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            text = response.text.strip()
            if not text:
                print(f"⚠ 固定接口内容为空")
                return

            lines = text.splitlines()
            current_group = "其他"
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("#EXTINF"):
                        name_match = re.match(r'#EXTINF:.*?,(.*)', line)
                        channel_name = name_match.group(1).strip() if name_match else "未知"
                    elif line.startswith("#EXTGRP"):
                        grp_match = re.match(r'#EXTGRP:(.*)', line)
                        if grp_match:
                            current_group = grp_match.group(1).strip()
                        continue
                    elif line.startswith("#"):
                        continue
                    else:
                        url = line.strip()
                        if not url.startswith("http"):
                            continue
                        unique_key = f"{channel_name}|{url}"
                        if unique_key not in self.channel_dict:
                            futures.append((current_group, channel_name, url, executor.submit(self._speed_test, url)))

                for group, name, url, future in futures:
                    speed = future.result()
                    if speed > 0:
                        if group not in self.fixed_groups:
                            self.fixed_groups[group] = []
                        self.fixed_groups[group].append(f"{name},{url}")
                        self.channel_dict[f"{name}|{url}"] = f"{name},{url}"
                        print(f"✔ [{group}] {name.ljust(20)} {speed} KB/s")
                    else:
                        print(f"✘ [{group}] {name.ljust(20)} 速度不足 {speed} KB/s")

        except Exception as e:
            print(f"⚠ 固定接口处理失败: {str(e)}")

    def _save_channels(self):
        cctv_list, satellite_list, others_list = [], [], []
        cctv_pattern = re.compile(r"CCTV[\-\s]?(\d{1,2}\+?|[4-8]K|UHD|HD|4K|8K)", re.I)
        satellite_pattern = re.compile(r"([\u4e00-\u9fa5]{2,4}卫视)(?:高清|标清|\+?)?台?")

        for line in self.channel_dict.values():
            name, url = line.split(',', 1)
            if cctv_match := cctv_pattern.search(name):
                num = cctv_match.group(1).upper()
                display_name = f"CCTV-{num}" if num.isdigit() else f"CCTV{num}"
                cctv_list.append(f"{display_name},{url}")
            elif sat_match := satellite_pattern.search(name):
                sat_name = sat_match.group(1)
                satellite_list.append(f"{sat_name},{url}")
            else:
                others_list.append(line)

        cctv_list.sort(key=lambda x: int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 999)
        satellite_list = sorted(list(set(satellite_list)))
        others_list = sorted(list(set(others_list)))

        try:
            with open("zby.txt", "w", encoding="utf-8") as f:
                f.write(f"# 最后更新时间: {time.strftime('%Y-%m-%d %H:%M')}\n\n")
                f.write("央视频道,#genre#\n")
                f.write("\n".join(cctv_list) + "\n\n")
                f.write("卫视频道,#genre#\n")
                f.write("\n".join(satellite_list) + "\n\n")
                f.write("其他频道,#genre#\n")
                f.write("\n".join(others_list) + "\n\n")

                # 固定接口分组输出
                if self.fixed_groups:
                    for group_name, channels in self.fixed_groups.items():
                        f.write(f"{group_name},#genre#\n")
                        f.write("\n".join(channels) + "\n\n")

            print(f"\n✅ 成功写入文件，总计频道数：{len(self.channel_dict)}")
            print(f"文件路径：{os.path.abspath('zby.txt')}")
            print(f"文件大小：{os.path.getsize('zby.txt') / 1024:.2f} KB")
        except Exception as e:
            print(f"⛔ 文件写入失败: {str(e)}")
            raise

    def run(self):
        print("\n" + "="*40)
        print(" IPTV列表更新程序启动 ".center(40, "★"))
        print("="*40)

        print("\n步骤1/4：获取源数据")
        api_urls = self._fetch_sources()
        print(f"找到有效API端点：{len(api_urls)}个")

        print("\n步骤2/4：处理API端点")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(self._process_api, api_urls)

        print("\n步骤2.1/4：处理固定接口")
        fixed_api = "https://raw.githubusercontent.com/xiaolin330328/ctv/main/第二"  # txt 文件无后缀
        self._process_fixed_api(fixed_api)

        print("\n步骤3/4：整理频道数据")
        self._save_channels()

        print("\n步骤4/4：完成更新")
        print(f"{'='*40}\n{' 更新完成 '.center(40, '☆')}\n{'='*40}")

if __name__ == "__main__":
    try:
        updater = IPTVUpdater()
        updater.run()
    except Exception as e:
        print(f"\n⛔ 程序异常终止: {str(e)}")
        exit(1)
