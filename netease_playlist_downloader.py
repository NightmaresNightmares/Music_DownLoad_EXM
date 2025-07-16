import os
import requests
from tqdm import tqdm
import json
import re

# 读取配置文件
with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)
# API 基础地址（可在 config.json 中修改）
API_BASE = config.get('API_BASE', 'https://163api.qijieya.cn')
# MODE=1 下载单曲，MODE=2 下载歌单
MODE = int(config.get('MODE', 2))
# 歌单ID（MODE=2时生效，支持链接或纯ID）
PLAYLIST_ID_RAW = config.get('PLAYLIST_ID', '947835566')
# 歌曲ID（MODE=1时生效，支持链接或纯ID）
SONG_ID_RAW = config.get('SONG_ID', '')

# 自动提取id参数

def extract_id(val):
    """
    从链接或纯数字中提取网易云id
    :param val: 歌单/歌曲链接或纯id
    :return: id字符串
    """
    if isinstance(val, int) or (isinstance(val, str) and val.isdigit()):
        return str(val)
    match = re.search(r'id=(\d+)', str(val))
    if match:
        return match.group(1)
    return str(val)

PLAYLIST_ID = extract_id(PLAYLIST_ID_RAW)
SONG_ID = extract_id(SONG_ID_RAW)

# 保存目录，自动创建在当前根目录下的 Music_DownLoad 文件夹
SAVE_DIR = os.path.join(os.getcwd(), 'Music_DownLoad')
# 每次请求获取的最大歌曲数（API限制）
SONGS_PER_REQUEST = 100

# 自动创建保存目录
os.makedirs(SAVE_DIR, exist_ok=True)

def get_all_tracks(playlist_id):
    """
    分页获取歌单内所有歌曲的详细信息
    :param playlist_id: 歌单ID
    :return: 歌曲信息列表
    """
    tracks = []
    offset = 0
    while True:
        url = f"{API_BASE}/playlist/track/all?id={playlist_id}&limit={SONGS_PER_REQUEST}&offset={offset}"
        resp = requests.get(url)
        data = resp.json()
        if 'songs' not in data or not data['songs']:
            break
        tracks.extend(data['songs'])
        if len(data['songs']) < SONGS_PER_REQUEST:
            break
        offset += SONGS_PER_REQUEST
    return tracks

def get_song_urls(song_ids):
    """
    批量获取歌曲的下载链接
    :param song_ids: 歌曲ID列表
    :return: {歌曲ID: 下载链接} 字典
    """
    urls = {}
    for i in range(0, len(song_ids), 100):
        batch = song_ids[i:i+100]
        ids_str = ','.join(str(sid) for sid in batch)
        url = f"{API_BASE}/song/url?id={ids_str}"
        resp = requests.get(url)
        data = resp.json()
        for item in data.get('data', []):
            urls[item['id']] = item['url']
    return urls

def sanitize_filename(name):
    """
    过滤非法文件名字符，防止保存失败
    :param name: 原始文件名
    :return: 合法文件名
    """
    return ''.join(c for c in name if c not in '\\/:*?\"<>|')

def download_song(song, url):
    """
    下载单首歌曲到本地
    :param song: 歌曲信息字典
    :param url: 下载链接
    """
    if not url:
        print(f"[跳过] {song['name']} - {song['ar'][0]['name']} (无下载链接)")
        return
    artist = song['ar'][0]['name']
    name = song['name']
    filename = sanitize_filename(f"{artist}-{name}.mp3")
    filepath = os.path.join(SAVE_DIR, filename)
    if os.path.exists(filepath):
        print(f"[已存在] {filename}")
        return
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            with open(filepath, 'wb') as f, tqdm(
                desc=filename, total=total, unit='B', unit_scale=True, unit_divisor=1024
            ) as bar:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        bar.update(len(chunk))
        print(f"[完成] {filename}")
    except Exception as e:
        print(f"[失败] {filename}: {e}")

def get_single_song(song_id):
    """
    获取单首歌曲的详细信息
    :param song_id: 歌曲ID
    :return: 歌曲信息字典或None
    """
    url = f"{API_BASE}/song/detail?ids={song_id}"
    resp = requests.get(url)
    data = resp.json()
    if 'songs' in data and data['songs']:
        return data['songs'][0]
    return None

def main():
    """
    主流程：根据MODE判断下载单曲还是歌单
    """
    if MODE == 1:
        # 下载单曲
        if not SONG_ID:
            print("请在config.json中设置SONG_ID！")
            return
        print(f"正在获取歌曲（ID: {SONG_ID}）...")
        song = get_single_song(SONG_ID)
        if not song:
            print("未找到该歌曲！")
            return
        print("正在获取下载链接...")
        urls = get_song_urls([song['id']])
        url = urls.get(song['id'])
        download_song(song, url)
        print("下载完成！")
    else:
        # 下载歌单
        print(f"正在获取歌单（ID: {PLAYLIST_ID}）的所有歌曲...")
        tracks = get_all_tracks(PLAYLIST_ID)
        print(f"共获取到 {len(tracks)} 首歌曲。")
        song_ids = [song['id'] for song in tracks]
        print("正在获取下载链接...")
        urls = get_song_urls(song_ids)
        print("开始下载...")
        for song in tracks:
            url = urls.get(song['id'])
            download_song(song, url)
        print("全部下载完成！")

if __name__ == '__main__':
    main() 