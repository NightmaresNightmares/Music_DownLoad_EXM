import os
import re
import threading
import requests
from flask import Flask, render_template_string, request, jsonify, Response, stream_with_context, session, make_response, send_from_directory
import time
import urllib.parse
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# 下载保存目录
SAVE_DIR = os.path.join(os.getcwd(), 'Music_DownLoad')
os.makedirs(SAVE_DIR, exist_ok=True)

# 全局任务队列和状态
download_queue = []  # [{'type': 'song'/'playlist', 'id': xxx, 'info': {...}}]
progress = {
    'status': 'idle',  # idle, downloading, done, error
    'current': 0,
    'total': 0,
    'msg': '',
    'detail': '',
    'now': None  # 当前下载歌曲/歌单信息
}

API_BASE = 'https://163api.qijieya.cn'
SONGS_PER_REQUEST = 1000  # 每次请求歌单歌曲的最大数量

# 工具函数

def extract_id(val):
    if isinstance(val, int) or (isinstance(val, str) and val.isdigit()):
        return str(val)
    match = re.search(r'id=(\d+)', str(val))
    if match:
        return match.group(1)
    return str(val)

def sanitize_filename(name):
    return ''.join(c for c in name if c not in '\\/:*?\"<>|')

def get_song_detail(song_id):
    url = f"{API_BASE}/song/detail?ids={song_id}"
    resp = requests.get(url)
    data = resp.json()
    if 'songs' in data and data['songs']:
        return data['songs'][0]
    return None

def get_playlist_detail(playlist_id):
    url = f"{API_BASE}/playlist/detail?id={playlist_id}"
    resp = requests.get(url)
    data = resp.json()
    if 'playlist' in data:
        return data['playlist']
    return None

def get_all_tracks(playlist_id):
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

def download_song(song, url):
    artist = song['ar'][0]['name']
    name = song['name']
    filename = sanitize_filename(f"{artist}-{name}.mp3")
    filepath = os.path.join(SAVE_DIR, filename)
    if os.path.exists(filepath):
        return f"[已存在] {filename}"
    if not url:
        return f"[跳过] {filename} (无下载链接)"
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return f"[完成] {filename}"
    except Exception as e:
        return f"[失败] {filename}: {e}"

def search_api(keyword, stype):
    url = f"{API_BASE}/search?keywords={keyword}&type={stype}"
    resp = requests.get(url)
    return resp.json()

COOKIE_DIR = os.path.join(os.getcwd(), 'cookies')
os.makedirs(COOKIE_DIR, exist_ok=True)

# 获取当前用户的 uniqid
USER_KEY = 'netease_user_key'
def get_user_key():
    return session.get('netease_user_key')

def set_user_key(uniqid):
    session['netease_user_key'] = uniqid
    session.permanent = True

def get_cookie():
    uniqid = get_user_key()
    if not uniqid:
        return ''
    cookie_path = os.path.join(COOKIE_DIR, f'{uniqid}.json')
    if not os.path.exists(cookie_path):
        return ''
    with open(cookie_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        raw_cookie = data.get('cookie', '')
        wanted_keys = ['MUSIC_U', '__csrf', 'NMTID']
        cookie_parts = []
        for key in wanted_keys:
            m = re.search(rf'{key}=([^;]+)', raw_cookie)
            if m:
                cookie_parts.append(f'{key}={m.group(1)}')
        return '; '.join(cookie_parts)

@app.before_request
def make_session_permanent():
    session.permanent = True

@app.route('/api/qr_key')
def qr_key():
    resp = requests.get(f'{API_BASE}/login/qr/key', params={'timestamp': int(time.time()*1000)})
    return jsonify(resp.json())

@app.route('/api/qr_create')
def qr_create():
    key = request.args.get('key')
    resp = requests.get(f'{API_BASE}/login/qr/create', params={'key': key, 'qrimg': 'true', 'timestamp': int(time.time()*1000)})
    return jsonify(resp.json())

@app.route('/api/qr_check')
def qr_check():
    key = request.args.get('key')
    resp = requests.get(f'{API_BASE}/login/qr/check', params={'key': key, 'timestamp': int(time.time()*1000)})
    data = resp.json()
    if data.get('code') == 803 and 'cookie' in data:
        uniqid = str(int(time.time() * 1000)) + '_' + key
        cookie_path = os.path.join(COOKIE_DIR, f'{uniqid}.json')
        # 保存完整JSON，格式化美观
        with open(cookie_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        set_user_key(uniqid)
    return jsonify(data)

@app.route('/api/user_account')
def user_account():
    print('当前用户uniqid:', get_user_key())
    print('当前cookie:', get_cookie())
    cookies = get_cookie()
    headers = {'Cookie': cookies}
    resp = requests.get(f'{API_BASE}/user/account', headers=headers)
    return jsonify(resp.json())

@app.route('/api/playlist_tracks')
def playlist_tracks():
    pid = request.args.get('id')
    cookies = get_cookie()
    headers = {'Cookie': cookies}
    # 自动翻页获取全部歌曲
    all_tracks = []
    offset = 0
    limit = 1000
    while True:
        resp = requests.get(f'{API_BASE}/playlist/track/all', params={'id': pid, 'limit': limit, 'offset': offset}, headers=headers)
        data = resp.json()
        if 'songs' not in data or not data['songs']:
            break
        all_tracks.extend(data['songs'])
        if len(data['songs']) < limit:
            break
        offset += limit
    return jsonify({'songs': all_tracks})

@app.route('/proxy_download/<song_id>')
def proxy_download(song_id):
    url_api = f'{API_BASE}/song/url?id={song_id}'
    resp = requests.get(url_api)
    data = resp.json()
    song_url = data['data'][0]['url']
    if not song_url:
        return '无法获取下载链接', 404
    detail_api = f'{API_BASE}/song/detail?ids={song_id}'
    detail = requests.get(detail_api).json()
    song = detail['songs'][0]
    artists = song.get('artists') or song.get('ar')
    filename = f"{artists[0]['name']}-{song['name']}.mp3"
    quoted_filename = urllib.parse.quote(filename)
    def generate():
        with requests.get(song_url, stream=True) as r:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
    headers = {
        'Content-Disposition': f"attachment; filename*=UTF-8''{quoted_filename}"
    }
    return Response(stream_with_context(generate()), headers=headers, content_type='audio/mpeg')

@app.route('/api/song_detail')
def api_song_detail():
    ids = request.args.get('ids')
    resp = requests.get(f'{API_BASE}/song/detail?ids={ids}')
    return jsonify(resp.json())

# 下载线程

def download_worker():
    while True:
        if not download_queue:
            progress.update({'status': 'idle', 'current': 0, 'total': 0, 'msg': '', 'now': None})
            break
        task = download_queue.pop(0)
        if task['type'] == 'song':
            song = get_song_detail(task['id'])
            if not song:
                progress.update({'status': 'error', 'msg': '未找到该歌曲', 'now': None})
                continue
            urls = get_song_urls([song['id']])
            url = urls.get(song['id'])
            progress.update({'status': 'downloading', 'current': 1, 'total': 1, 'now': song, 'msg': ''})
            msg = download_song(song, url)
            progress.update({'current': 1, 'msg': msg, 'status': 'done'})
        elif task['type'] == 'playlist':
            tracks = get_all_tracks(task['id'])
            total = len(tracks)
            progress.update({'status': 'downloading', 'current': 0, 'total': total, 'now': task['info'], 'msg': ''})
            if not tracks:
                progress.update({'status': 'error', 'msg': '歌单无歌曲或获取失败', 'now': None})
                continue
            song_ids = [song['id'] for song in tracks]
            urls = get_song_urls(song_ids)
            for idx, song in enumerate(tracks, 1):
                url = urls.get(song['id'])
                msg = download_song(song, url)
                progress.update({'current': idx, 'msg': msg, 'now': song})
            progress.update({'status': 'done', 'msg': '全部下载完成！', 'now': None})

# ----------------- Flask 路由 -----------------

HTML = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>网易云音乐下载器</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #f8f9fa; }
        .container { max-width: 700px; margin-top: 40px; }
        .progress { height: 30px; }
        .status-area { min-height: 40px; margin-top: 10px; }
        .cover-img { width: 80px; height: 80px; object-fit: cover; border-radius: 8px; border: 2px solid #0d6efd; }
        .song-card, .playlist-card { background: #fff; border: 1px solid #e3eafc; border-radius: 12px; box-shadow: 0 2px 8px #e3eafc55; margin-bottom: 16px; padding: 16px; }
        .queue-list { list-style: none; padding: 0; }
        .queue-list li { background: #e9f2ff; border-radius: 8px; margin-bottom: 8px; padding: 8px 12px; }
        .btn-primary { background: #0d6efd; border: none; }
        .btn-primary:hover { background: #0b5ed7; }
        .form-label { color: #0d6efd; }
    </style>
</head>
<body>
<div class="container shadow p-4 bg-white rounded">
    <h2 class="mb-4 text-primary">网易云音乐下载器</h2>
    <form id="search-form" class="row g-3 mb-4">
        <div class="col-md-5">
            <input type="text" class="form-control" id="search-keyword" placeholder="输入关键词搜索歌曲/歌单">
        </div>
        <div class="col-md-3">
            <select class="form-select" id="search-type">
                <option value="1">歌曲</option>
            </select>
        </div>
        <div class="col-md-2">
            <button type="submit" class="btn btn-primary w-100">搜索</button>
        </div>
    </form>
    <div id="search-result"></div>
    <hr>
    <h5 class="text-primary">下载队列</h5>
    <ul class="queue-list" id="queue-list"></ul>
    <button class="btn btn-primary mb-3 ms-2" onclick="batchSequentialDownload()">批量顺序下载</button>
    <div id="batch-download-status" class="mb-3 text-info"></div>
    <div class="status-area mt-3">
        <div id="now-info" class="mt-3"></div>
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script>
// 试听弹窗UI
function showPreviewModal(song) {
    if(document.getElementById('preview-modal')) document.getElementById('preview-modal').remove();
    let cover = song.cover || (song.al && song.al.picUrl ? song.al.picUrl : '');
    let imgHtml = cover ? `<img src="${cover}" style="width:80px;height:80px;border-radius:8px;border:2px solid #0d6efd;object-fit:cover;">` : '';
    let modal = document.createElement('div');
    modal.id = 'preview-modal';
    modal.innerHTML = `
    <div style="position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.4);z-index:9999;display:flex;align-items:center;justify-content:center;">
      <div style="background:#fff;border-radius:16px;box-shadow:0 4px 32px #0002;padding:32px 24px;min-width:320px;max-width:90vw;position:relative;">
        <button onclick="document.getElementById('preview-modal').remove()" style="position:absolute;top:8px;right:12px;font-size:22px;border:none;background:none;">×</button>
        <div class="d-flex align-items-center mb-3">
          ${imgHtml}
          <div class="ms-3">
            <b style="font-size:1.2em;">${song.name}</b><br>
            <span class="text-secondary">${song.artist}</span>
          </div>
        </div>
        <audio id="audio-preview" src="/proxy_download/${song.id}" controls style="width:100%;"></audio>
        <div class="mt-2">
          <input type="range" id="audio-progress" value="0" min="0" max="100" style="width:100%;">
        </div>
        <div class="d-flex justify-content-between text-secondary small mt-1">
          <span id="audio-current">00:00</span>
          <span id="audio-duration">00:00</span>
        </div>
      </div>
    </div>`;
    document.body.appendChild(modal);
    let audio = document.getElementById('audio-preview');
    let progress = document.getElementById('audio-progress');
    let current = document.getElementById('audio-current');
    let duration = document.getElementById('audio-duration');
    audio.ontimeupdate = function() {
      if(audio.duration) progress.value = audio.currentTime / audio.duration * 100;
      current.innerText = formatTime(audio.currentTime);
      duration.innerText = formatTime(audio.duration);
    };
    progress.oninput = function() {
      if(audio.duration) audio.currentTime = progress.value / 100 * audio.duration;
    };
    function formatTime(sec) {
      if(isNaN(sec)) return '00:00';
      let m = Math.floor(sec/60), s = Math.floor(sec%60);
      return (m<10?'0':'')+m+':' + (s<10?'0':'')+s;
    }
}
let queue = [];
function renderQueue() {
    let ql = document.getElementById('queue-list');
    ql.innerHTML = '';
    queue.forEach((item, idx) => {
        let info = item.info;
        let cover = info.al && info.al.picUrl ? info.al.picUrl : (info.cover ? info.cover : '');
        let imgHtml = cover ? `<img class='cover-img' src='${cover}'>` : '';
        let html = `${imgHtml}<b>${item.type === 'song' ? '🎵' : '📀'} ${info.name}</b> <span class='text-secondary'>${info.artist||info.creator||''}</span>
        <button class='btn btn-sm btn-outline-danger float-end ms-2' onclick='removeFromQueue(${idx})'>移除</button>`;
        if(item.type === 'song' && item.id) {
            html += ` <a href="/proxy_download/${item.id}" class="btn btn-success btn-sm float-end" style="margin-right:8px;" download><svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='currentColor' class='bi bi-download' viewBox='0 0 16 16'><path d='M.5 9.9a.5.5 0 0 1 .5.5v2.5A1.5 1.5 0 0 0 2.5 14h11a1.5 1.5 0 0 0 1.5-1.5V10.4a.5.5 0 0 1 1 0v2.1A2.5 2.5 0 0 1 13.5 15h-11A2.5 2.5 0 0 1 0 12.5V10.4a.5.5 0 0 1 .5-.5z'/><path d='M7.646 11.854a.5.5 0 0 0 .708 0l3-3a.5.5 0 0 0-.708-.708L8.5 10.293V1.5a.5.5 0 0 0-1 0v8.793L5.354 8.146a.5.5 0 1 0-.708.708l3 3z'/></svg> 下载</a>`;
            html += ` <button class="btn btn-info btn-sm float-end" style="margin-right:8px;" onclick='showPreviewModal(${JSON.stringify({id:item.id,name:info.name,artist:info.artist,cover:cover})})'>试听</button>`;
        }
        let li = document.createElement('li');
        li.innerHTML = html;
        ql.appendChild(li);
    });
}
function removeFromQueue(idx) {
    queue.splice(idx, 1);
    renderQueue();
}
document.getElementById('search-form').onsubmit = function(e) {
    e.preventDefault();
    let kw = document.getElementById('search-keyword').value.trim();
    let stype = document.getElementById('search-type').value;
    if(!kw) return;
    let defaultCover = 'https://via.placeholder.com/60x60?text=No+Cover';
    fetch(`/search?kw=${encodeURIComponent(kw)}&stype=${stype}`).then(r=>r.json()).then(data=>{
        let res = document.getElementById('search-result');
        res.innerHTML = '';
        if(stype==='1' && data.result && data.result.songs) {
            let ids = data.result.songs.map(song => song.id).join(',');
            fetch(`/api/song_detail?ids=${ids}`).then(r=>r.json()).then(detailData => {
                let id2cover = {};
                detailData.songs.forEach(song => {
                    id2cover[song.id] = song.al && song.al.picUrl ? song.al.picUrl : defaultCover;
                });
                data.result.songs.forEach((song, idx) => {
                    let cover = id2cover[song.id] || defaultCover;
                    let btnId = `add-btn-${song.id}`;
                    let imgHtml = `<img class='cover-img' src='${cover}'>`;
                    let html = `<div class='song-card row align-items-center'>
                        <div class='col-auto'>${imgHtml}</div>
                        <div class='col'>
                            <b>${song.name}</b><br>
                            <span class='text-secondary'>${song.artists.map(a=>a.name).join("/")}</span><br>
                            <span class='text-secondary'>${song.album.name}</span>
                        </div>
                        <div class='col-auto d-flex flex-column gap-2'>
                            <button id='${btnId}' class='btn btn-primary mb-1' onclick='addToQueueUI(this, "song", "${song.id}", ${JSON.stringify({name:song.name,artist:song.artists.map(a=>a.name).join("/"),cover:cover})})'>加入队列</button>
                            <a href="/proxy_download/${song.id}" class="btn btn-success btn-sm" target="_blank">下载</a>
                            <button class="btn btn-info btn-sm" onclick='showPreviewModal(${JSON.stringify({id:song.id,name:song.name,artist:song.artists.map(a=>a.name).join("/"),cover:cover})})'>试听</button>
                        </div>
                    </div>`;
                    res.innerHTML += html;
                });
            });
        } else if(stype==='1000' && data.result && data.result.playlists) {
            data.result.playlists.forEach(pl=>{
                let cover = pl.coverImgUrl ? pl.coverImgUrl : defaultCover;
                let html = `<div class='playlist-card row align-items-center'>
                    <div class='col-auto'><img class='cover-img' src='${cover}'></div>
                    <div class='col'>
                        <b>${pl.name}</b><br>
                        <span class='text-secondary'>by ${pl.creator.nickname}</span><br>
                        <span class='text-secondary'>${pl.trackCount} 首歌</span>
                    </div>
                    <div class='col-auto'>
                        <button class='btn btn-primary' onclick='fetchPlaylistSongs(${pl.id})'>查看歌单详情</button>
                    </div>
                </div>
                <div id='playlist-detail-${pl.id}'></div>`;
                res.innerHTML += html;
            });
        } else {
            res.innerHTML = '<div class="text-danger">未找到结果</div>';
        }
    });
};
function addToQueueUI(btn, type, id, info) {
    queue.push({type, id, info});
    renderQueue();
    btn.classList.remove('btn-primary');
    btn.classList.add('btn-danger');
    btn.innerText = '已加入队列';
    btn.disabled = true;
}
document.getElementById('start-btn').onclick = function() {
    if(queue.length===0) return alert('请先添加任务到队列！');
    fetch('/start', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({queue})
    }).then(r=>r.json()).then(data=>{
        updateStatus();
    });
};
function updateStatus() {
    fetch('/status').then(r=>r.json()).then(data=>{
        let bar = document.getElementById('progress-bar');
        let text = document.getElementById('status-text');
        let percent = data.total ? Math.floor(data.current * 100 / data.total) : 0;
        bar.style.width = percent + '%';
        bar.innerText = percent + '%';
        text.innerText = data.status === 'downloading' ? `下载中：${data.current}/${data.total}  ${data.msg}` : data.msg;
        if(data.status === 'done') {
            bar.classList.add('bg-success');
        } else if(data.status === 'error') {
            bar.classList.add('bg-danger');
        } else {
            bar.classList.remove('bg-success','bg-danger');
        }
        // 当前歌曲/歌单信息
        let now = data.now;
        let nowinfo = document.getElementById('now-info');
        if(now && now.album && now.album.picUrl) {
            let cover = now.album.picUrl ? now.album.picUrl : null;
            let imgHtml = cover ? `<img class='cover-img me-3' src='${cover}'>` : '';
            nowinfo.innerHTML = `${imgHtml}<b>${now.name}</b> <span class='text-secondary'>${now.artists.map(a=>a.name).join('/')}</span>`;
        } else if(now && now.coverImgUrl) {
            let cover = now.coverImgUrl ? now.coverImgUrl : null;
            let imgHtml = cover ? `<img class='cover-img me-3' src='${cover}'>` : '';
            nowinfo.innerHTML = `${imgHtml}<b>${now.name}</b>`;
        } else {
            nowinfo.innerHTML = '';
        }
    });
}
setInterval(updateStatus, 2000);
updateStatus();
renderQueue();

function batchSequentialDownload() {
    if (queue.length === 0) {
        alert('队列为空！');
        return;
    }
    let statusText = document.getElementById('batch-download-status');
    let i = 0;
    function downloadNext() {
        if (i >= queue.length) {
            statusText.innerText = '全部下载完成！';
            return;
        }
        let item = queue[i];
        if (item.type === 'song' && item.id) {
            statusText.innerText = `正在下载第${i+1}首：${item.info.name}`;
            // 创建临时a标签并点击
            let a = document.createElement('a');
            a.href = `/proxy_download/${item.id}`;
            a.download = '';
            a.target = '_blank';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }
        i++;
        setTimeout(downloadNext, 3000); // 3秒后下载下一个
    }
    downloadNext();
}

// ========== 补充：获取歌单全部歌曲 ==========
function fetchPlaylistSongs(pid) {
    checkLoginStatus(function(loggedIn){
        if(!loggedIn) { showQrLoginModal(); return; }
        fetch(`/api/playlist_tracks?id=${pid}&limit=1000`)
          .then(r => r.json())
          .then(data => {
              if(!data.songs || !data.songs.length) {
                  document.getElementById('playlist-info').innerHTML = '<span class="text-danger">未获取到歌曲，可能未登录或歌单不存在</span>';
                  document.getElementById('song-list').innerHTML = '';
                  allSongs = [];
                  queue = [];
                  renderQueue();
                  renderSongPagination();
                  return;
              }
              document.getElementById('playlist-info').innerHTML = `<b>共${data.songs.length}首歌</b>`;
              allSongs = data.songs;
              currentPage = 1;
              renderSongList();
              renderQueue();
              renderSongPagination();
          });
    });
}
window.fetchPlaylistSongs = fetchPlaylistSongs;
</script>
</body>
</html>
'''

@app.route('/', methods=['GET'])
def main_new_ui():
    return new_ui()

@app.route('/old_ui', methods=['GET'])
def old_ui():
    return render_template_string(HTML)

@app.route('/playlist_downloader', methods=['GET'])
def playlist_downloader():
    return '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>网易云歌单全量下载</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #f8f9fa; }
        .container { max-width: 800px; margin-top: 40px; }
        .cover-img { width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 2px solid #0d6efd; }
        /* 队列区卡片更明显 */
        .queue-list { list-style: none; padding: 0; }
        .queue-list li.queue-item {
            background: #eaf4ff;
            border: 2px solid #90caf9;
            border-radius: 14px;
            box-shadow: 0 4px 16px #90caf955;
            margin-bottom: 18px;
            padding: 14px 18px;
        }
        /* 歌曲列表区卡片 */
        .song-row {
            background: #fff;
            border: 1.5px solid #e3eafc;
            border-radius: 12px;
            box-shadow: 0 2px 8px #e3eafc55;
            margin-bottom: 16px;
            padding: 14px 18px;
        }
        .btn-primary { background: #0d6efd; border: none; }
        .btn-primary:hover { background: #0b5ed7; }
        .form-label { color: #0d6efd; }
        .qr-modal-bg { position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.4);z-index:9999;display:flex;align-items:center;justify-content:center; }
        .qr-modal-box { background:#fff;border-radius:16px;box-shadow:0 4px 32px #0002;padding:32px 24px;min-width:320px;max-width:90vw;position:relative; animation: popIn 0.3s; }
    </style>
</head>
<body>
<div class="container shadow p-4 bg-white rounded">
    <h2 class="mb-4 text-primary">网易云歌单全量下载</h2>
    <div class="mb-3">
        <label class="form-label text-primary">请输入歌单ID或链接：</label>
        <div class="input-group">
            <input type="text" class="form-control" id="playlist-input" placeholder="如 24381616 或 https://music.163.com/#/playlist?id=24381616">
            <button class="btn btn-primary" id="fetch-btn">获取全部歌曲</button>
        </div>
    </div>
    <div id="login-status" class="mb-3 text-info"></div>
    <div id="playlist-info" class="mb-3"></div>
    <div class="mb-3">
        <button class="btn btn-success" id="add-all-btn" style="display:none;">全部加入队列</button>
        <button class="btn btn-primary ms-2" id="batch-download-btn" style="display:none;">下载队列歌曲</button>
        <span id="batch-download-status" class="ms-3 text-info"></span>
    </div>
    <div class="row">
        <div class="col-md-7">
            <div id="song-list"></div>
        </div>
        <div class="col-md-5">
            <div id="queue-section" style="display:none;">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <h5 class="text-primary mb-0" id="queue-title">下载队列</h5>
                    <div>
                        <button class="btn btn-success btn-sm me-2" id="add-all-btn" style="display:none;">全部加入队列</button>
                        <button class="btn btn-primary btn-sm" id="batch-download-btn" style="display:none;">下载队列歌曲</button>
                    </div>
                </div>
                <span id="batch-download-status" class="ms-2 text-info"></span>
                <ul class="queue-list" id="queue-list"></ul>
            </div>
        </div>
    </div>
    <hr id="divider-line" style="display:none;"/>
</div>
<div id="qr-modal" style="display:none;"></div>
<div id="preview-modal"></div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script>
let allSongs = [];
let queue = [];
function extractPlaylistId(input) {
    let match = input.match(/playlist\\?id=(\\d+)/);
    if (match) return match[1];
    if (/^\\d+$/.test(input)) return input;
    return null;
}
function showQrLoginModal() {
    let modal = document.getElementById('qr-modal');
    modal.style.display = '';
    modal.innerHTML = `<div class='qr-modal-bg'><div class='qr-modal-box'><div id='qr-img-box' class='text-center mb-2'></div><div id='qr-status' class='text-center text-info mb-2'>请使用网易云音乐App扫码登录</div><button class='btn btn-sm btn-outline-secondary' onclick='closeQrModal()' style='position:absolute;top:8px;right:12px;'>关闭</button></div></div>`;
    fetch('/api/qr_key').then(r=>r.json()).then(data=>{
        let key = data.data.unikey;
        fetch(`/api/qr_create?key=${key}`).then(r=>r.json()).then(data=>{
            let qrimg = data.data.qrimg;
            document.getElementById('qr-img-box').innerHTML = `<img src='${qrimg}' style='width:180px;height:180px;'>`;
            pollQrStatus(key);
        });
    });
}
function closeQrModal() {
    document.getElementById('qr-modal').style.display = 'none';
}
function pollQrStatus(key) {
    let statusDiv = document.getElementById('qr-status');
    let timer = setInterval(()=>{
        fetch(`/api/qr_check?key=${key}`).then(r=>r.json()).then(data=>{
            if(data.code === 800) {
                statusDiv.innerText = '二维码已过期，请关闭后重试';
                clearInterval(timer);
            } else if(data.code === 801) {
                statusDiv.innerText = '等待扫码...';
            } else if(data.code === 802) {
                statusDiv.innerText = '请在手机上确认登录';
            } else if(data.code === 803) {
                statusDiv.innerText = '登录成功！';
                setTimeout(()=>{ closeQrModal(); location.reload(); }, 1000);
                clearInterval(timer);
            }
        });
    }, 2000);
}
function checkLoginStatus(cb) {
    fetch('/api/user_account').then(r=>r.json()).then(data=>{
        if(data.code === 200 && data.profile && data.profile.nickname) {
            document.getElementById('login-status').innerText = `已登录：${data.profile.nickname}`;
            cb && cb(true);
        } else {
            document.getElementById('login-status').innerHTML = `<span class='text-danger'>未登录，请先 <a href='#' onclick='showQrLoginModal()'>扫码登录</a></span>`;
            cb && cb(false);
        }
    });
}
function renderSongList() {
    let html = '';
    allSongs.forEach((song, idx)=>{
        let cover = song.al && song.al.picUrl ? song.al.picUrl : '';
        let artists = song.ar ? song.ar.map(a=>a.name).join('/') : '';
        let inQueue = queue.find(q=>q.id===song.id);
        html += `<div class='song-row row align-items-center'>
            <div class='col-auto'>${cover?`<img class='cover-img' src='${cover}'>`:''}</div>
            <div class='col'>
                <b>${idx+1}. ${song.name}</b><br>
                <span class='text-secondary'>${artists}</span>
            </div>
            <div class='col-auto d-flex flex-column gap-2'>
                <button class='btn btn-${inQueue?'danger':'primary'} btn-sm mb-1' onclick='toggleQueue(${JSON.stringify(song)})'>${inQueue?'移除队列':'加入队列'}</button>
                <a href="/proxy_download/${song.id}" class="btn btn-success btn-sm mb-1" download target="_blank">下载</a>
                <button class="btn btn-info btn-sm" onclick='showPreviewModal(${JSON.stringify({id:song.id,name:song.name,artist:artists,cover:cover})})'>试听</button>
            </div>
        </div>`;
    });
    document.getElementById('song-list').innerHTML = html;
    // 控制分隔线和队列区显示
    document.getElementById('divider-line').style.display = allSongs.length ? '' : 'none';
    document.getElementById('queue-section').style.display = allSongs.length ? '' : 'none';
    // 全部加入队列按钮逻辑
    let allInQueue = allSongs.length > 0 && allSongs.every(song => queue.find(q => q.id === song.id));
    let addAllBtn = document.getElementById('add-all-btn');
    addAllBtn.style.display = allSongs.length ? '' : 'none';
    addAllBtn.disabled = allInQueue;
    addAllBtn.innerText = allInQueue ? '已全部加入' : '全部加入队列';
}
function renderQueue() {
    let ql = document.getElementById('queue-list');
    ql.innerHTML = '';
    queue.forEach((song, idx) => {
        let cover = song.al && song.al.picUrl ? song.al.picUrl : '';
        let artists = song.ar ? song.ar.map(a=>a.name).join('/') : '';
        let html = `<div class="row align-items-center">
            <div class="col-auto">
                ${cover?`<img class='cover-img' src='${cover}'>`:''}
            </div>
            <div class="col">
                <div class="fw-bold fs-6 mb-1">${song.name}</div>
                <div class="text-secondary small mb-2">${artists}</div>
                <div class="d-flex gap-2 justify-content-end">
                    <button class="btn btn-info btn-sm" onclick='showPreviewModal(${JSON.stringify({id:song.id,name:song.name,artist:artists,cover:cover})})'>试听</button>
                    <a href="/proxy_download/${song.id}" class="btn btn-success btn-sm" download target="_blank">下载</a>
                    <button class='btn btn-sm btn-outline-danger' onclick='removeFromQueue(${idx})'>移除</button>
                </div>
            </div>
        </div>`;
        let li = document.createElement('li');
        li.className = 'queue-item';
        li.innerHTML = html;
        ql.appendChild(li);
    });
    document.getElementById('batch-download-btn').style.display = queue.length ? '' : 'none';
    document.getElementById('remove-all-btn').style.display = queue.length ? '' : 'none';
}
function addToQueue(song) {
    if(!queue.find(q=>q.id===song.id)) {
        queue.push(song);
        renderSongList();
        renderQueue();
    }
}
function removeFromQueue(idx) {
    queue.splice(idx, 1);
    renderSongList();
    renderQueue();
}
function toggleQueue(song) {
    let idx = queue.findIndex(q=>q.id===song.id);
    if(idx === -1) {
        queue.push(song);
    } else {
        queue.splice(idx, 1);
    }
    renderSongList();
    renderQueue();
}
document.getElementById('add-all-btn').onclick = function() {
    allSongs.forEach(song=>addToQueue(song));
};
document.getElementById('batch-download-btn').onclick = function() {
    batchSequentialDownload();
};
function batchSequentialDownload() {
    if (queue.length === 0) {
        alert('队列为空！');
        return;
    }
    let statusText = document.getElementById('batch-download-status');
    let i = 0;
    function downloadNext() {
        if (i >= queue.length) {
            statusText.innerText = '全部下载完成！';
            return;
        }
        let item = queue[i];
        statusText.innerText = `正在下载第${i+1}首：${item.name}`;
        let a = document.createElement('a');
        a.href = `/proxy_download/${item.id}`;
        a.download = '';
        a.target = '_blank';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        i++;
        setTimeout(downloadNext, 3000);
    }
    downloadNext();
}
function showPreviewModal(song) {
    let modal = document.getElementById('preview-modal');
    modal.innerHTML = `<div style="position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.4);z-index:9999;display:flex;align-items:center;justify-content:center;">
      <div style="background:#fff;border-radius:16px;box-shadow:0 4px 32px #0002;padding:32px 24px;min-width:320px;max-width:90vw;position:relative;">
        <button onclick="document.getElementById('preview-modal').innerHTML=''" style="position:absolute;top:8px;right:12px;font-size:22px;border:none;background:none;">×</button>
        <div class="d-flex align-items-center mb-3">
          ${song.cover?`<img src='${song.cover}' style='width:80px;height:80px;border-radius:8px;border:2px solid #0d6efd;object-fit:cover;'>`:''}
          <div class="ms-3">
            <b style="font-size:1.2em;">${song.name}</b><br>
            <span class="text-secondary">${song.artist}</span>
          </div>
        </div>
        <audio id="audio-preview" src="/proxy_download/${song.id}" controls style="width:100%;"></audio>
      </div>
    </div>`;
}
document.getElementById('fetch-btn').onclick = function() {
    let val = document.getElementById('playlist-input').value.trim();
    let pid = extractPlaylistId(val);
    if(!pid) { alert('请输入正确的歌单ID或链接'); return; }
    checkLoginStatus(function(loggedIn){
        if(!loggedIn) { showQrLoginModal(); return; }
        fetch(`/api/playlist_tracks?id=${pid}&limit=1000`)
          .then(r => r.json())
          .then(data => {
              if(!data.songs || !data.songs.length) {
                  document.getElementById('playlist-info').innerHTML = '<span class="text-danger">未获取到歌曲，可能未登录或歌单不存在</span>';
                  document.getElementById('song-list').innerHTML = '';
                  allSongs = [];
                  queue = [];
                  renderQueue();
                  renderSongPagination();
                  return;
              }
              document.getElementById('playlist-info').innerHTML = `<b>共${data.songs.length}首歌</b>`;
              allSongs = data.songs;
              queue = [];
              renderSongList();
              renderQueue();
          });
    });
};
checkLoginStatus();

// ========== 补充：获取歌单全部歌曲 ==========
function fetchPlaylistSongs(pid) {
    checkLoginStatus(function(loggedIn){
        if(!loggedIn) { showQrLoginModal(); return; }
        fetch(`/api/playlist_tracks?id=${pid}&limit=1000`)
          .then(r => r.json())
          .then(data => {
              if(!data.songs || !data.songs.length) {
                  document.getElementById('playlist-info').innerHTML = '<span class="text-danger">未获取到歌曲，可能未登录或歌单不存在</span>';
                  document.getElementById('song-list').innerHTML = '';
                  allSongs = [];
                  queue = [];
                  renderQueue();
                  renderSongPagination();
                  return;
              }
              document.getElementById('playlist-info').innerHTML = `<b>共${data.songs.length}首歌</b>`;
              allSongs = data.songs;
              currentPage = 1;
              renderSongList();
              renderQueue();
              renderSongPagination();
          });
    });
}
window.fetchPlaylistSongs = fetchPlaylistSongs;
</script>
</body>
</html>
'''

@app.route('/search')
def search():
    kw = request.args.get('kw', '')
    stype = request.args.get('stype', '1')
    return jsonify(search_api(kw, stype))

@app.route('/new_ui')
def new_ui():
    return '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>网易云音乐多功能下载中心</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css">
    <style>
        body { background: linear-gradient(135deg, #f6d365 0%, #fda085 100%); min-height: 100vh; }
        .main-container { max-width: 1400px; margin: 48px auto; background: rgba(255,255,255,0.97); border-radius: 32px; box-shadow: 0 8px 32px #fda08555; padding: 48px 40px; }
        .big-title { font-size: 2.7em; font-weight: bold; background: linear-gradient(90deg,#fda085,#f6d365); -webkit-background-clip: text; color: transparent; letter-spacing: 2px; }
        .sub-title { font-size: 1.2em; color: #f6723a; margin-bottom: 32px; }
        .left-col { border-right: 2px dashed #fda08533; min-height: 600px; }
        .section-title { color: #f6723a; font-weight: bold; font-size: 1.2em; margin-bottom: 16px; letter-spacing: 1px; }
        .card-style { background: #fff7f0; border-radius: 16px; box-shadow: 0 4px 16px #fda08533; padding: 18px 20px; margin-bottom: 22px; }
        .cover-img { width: 44px; height: 44px; object-fit: cover; border-radius: 8px; border: 2px solid #f6723a; box-shadow: 0 2px 8px #fda08533; }
        .queue-list { list-style: none; padding: 0; }
        .queue-list li.queue-item { background: #ffe0c7; border: 2px solid #fda085; border-radius: 14px; box-shadow: 0 4px 16px #fda08533; margin-bottom: 14px; padding: 10px 14px; }
        .song-row { background: #fff; border: 2px solid #f6d365; border-radius: 12px; box-shadow: 0 2px 8px #fda08533; margin-bottom: 10px; padding: 8px 10px; font-size: 0.98em; min-height: 54px; }
        .song-row .col { font-size: 0.97em; }
        .song-row .btn { font-size: 0.92em; padding: 2px 10px; margin-bottom: 2px; }
        .btn-main { background: linear-gradient(90deg,#fda085,#f6d365); border: none; color: #fff; font-weight: bold; }
        .btn-main:hover { background: linear-gradient(90deg,#f6d365,#fda085); color: #fff; }
        .btn-icon { margin-right: 5px; }
        .form-label { color: #f6723a; font-weight: bold; }
        .info-card { background: #fffbe6; border-left: 6px solid #fda085; border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; color: #b85c00; font-size: 0.98em; }
        .qr-modal-bg { position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.4);z-index:9999;display:flex;align-items:center;justify-content:center; animation: fadeIn 0.3s; }
        .qr-modal-box { background:#fff;border-radius:16px;box-shadow:0 4px 32px #0002;padding:32px 24px;min-width:320px;max-width:90vw;position:relative; animation: popIn 0.3s; }
        .divider { border-top: 2px dashed #fda08533; margin: 32px 0; }
        .help-section { background: #f6d36522; border-radius: 12px; padding: 18px 22px; color: #b85c00; font-size: 1.05em; margin-top: 32px; }
        .copyright { color: #f6723a; font-size: 0.98em; margin-top: 18px; text-align: center; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes popIn { from { transform: scale(0.8); opacity: 0; } to { transform: scale(1); opacity: 1; } }
        .modal-anim { animation: fadeIn 0.3s; }
        .modal-content-anim { animation: popIn 0.3s; }
        .pagination { justify-content: center; margin-top: 10px; }
        .pagination .page-link { color: #f6723a; }
        .pagination .active .page-link { background: linear-gradient(90deg,#fda085,#f6d365); color: #fff; border: none; }
    </style>
</head>
<body>
<div class="main-container">
    <div class="text-center mb-4">
        <div class="big-title">网易云音乐多功能下载中心</div>
        <div class="sub-title">支持扫码登录、歌单/单曲全量获取、关键词搜索、队列管理、批量下载、试听、彩色卡片UI、弹窗提示等高级功能</div>
    </div>
    <div class="row">
        <div class="col-md-7 left-col">
            <div class="section-title"><i class="bi bi-search"></i> 关键词搜索区</div>
            <form id="search-form" class="card-style mb-4">
                <div class="mb-3">
                    <input type="text" class="form-control" id="search-keyword" placeholder="输入关键词搜索歌曲/歌单">
                </div>
                <div class="mb-3">
                    <select class="form-select" id="search-type">
                        <option value="1">歌曲</option>
                    </select>
                </div>
                <button type="submit" class="btn btn-main w-100"><i class="bi bi-search btn-icon"></i>搜索</button>
            </form>
            <div class="info-card">
                <b>小提示：</b><br>
                - 支持输入歌名、歌手、专辑、歌单名等关键词。<br>
                - 搜索结果可直接试听、下载、加入队列。<br>
                - 歌单搜索结果可一键获取全部歌曲。
            </div>
            <div id="search-result"></div>
            <div id="song-list"></div>
            <nav>
                <ul class="pagination" id="song-pagination"></ul>
            </nav>
        </div>
        <div class="col-md-5">
            <div class="section-title"><i class="bi bi-link-45deg"></i> 歌单ID/链接直达区</div>
            <div class="card-style mb-3">
                <label class="form-label">请输入歌单ID或链接：</label>
                <div class="input-group mb-2">
                    <input type="text" class="form-control" id="playlist-input" placeholder="如 24381616 或 https://music.163.com/#/playlist?id=24381616">
                    <button class="btn btn-main" id="fetch-btn"><i class="bi bi-lightning-fill btn-icon"></i>获取全部歌曲</button>
                </div>
                <div id="login-status" class="mb-2"></div>
                <div id="playlist-info" class="mb-2"></div>
                <div class="text-secondary small">如需获取私人歌单、收藏等，请先扫码登录。</div>
                <button class="btn btn-outline-danger btn-sm mt-2" id="qr-login-btn"><i class="bi bi-qr-code btn-icon"></i>扫码登录</button>
            </div>
            <div class="info-card">
                <b>操作说明：</b><br>
                - 支持输入歌单ID或完整链接，自动提取ID。<br>
                - 登录后可获取私人歌单、收藏、历史等。<br>
                - 歌单歌曲可全部加入队列，支持批量顺序下载。
            </div>
            <div class="mb-3">
                <button class="btn btn-success" id="add-all-btn" style="display:none;"><i class="bi bi-plus-circle btn-icon"></i>全部加入队列（本页）</button>
                <button class="btn btn-danger ms-2" id="remove-all-btn" style="display:none;"><i class="bi bi-trash btn-icon"></i>全部移除</button>
                <button class="btn btn-warning ms-2" id="batch-download-btn" style="display:none;"><i class="bi bi-download btn-icon"></i>批量顺序下载</button>
                <span id="batch-download-status" class="ms-3 text-info"></span>
            </div>
            <div class="divider"></div>
            <div class="section-title"><i class="bi bi-list-task"></i> 下载队列（全局唯一）</div>
            <div id="queue-section" class="card-style" style="display:none;">
                <ul class="queue-list" id="queue-list"></ul>
            </div>
        </div>
    </div>
    <div class="help-section mt-4">
        <b>帮助与说明：</b><br>
        1. 本工具仅供学习交流，严禁用于商业用途。<br>
        2. 支持扫码登录，获取私人歌单、收藏、历史等。<br>
        3. 支持关键词搜索、歌单ID/链接直达、队列管理、批量下载、试听等高级功能。<br>
        4. 队列区为全局唯一，支持批量顺序下载、试听、移除等操作。<br>
        5. 如遇问题请刷新页面或重新扫码登录。<br>
        6. 详细使用方法见页面各区块说明。
    </div>
    <div class="copyright">© 2024 网易云音乐多功能下载中心 | 仅供学习交流，严禁商用</div>
</div>
<div id="qr-modal" style="display:none;"></div>
<div id="preview-modal"></div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script>
// ========== 全局变量 ==========
let allSongs = [];
let queue = [];
let currentPage = 1;
const PAGE_SIZE = 10;

// ========== 工具函数 ==========
function extractPlaylistId(input) {
    let match = input.match(/playlist\?id=(\d+)/);
    if (match) return match[1];
    if (/^\d+$/.test(input)) return input;
    return null;
}
function getArtist(song) {
    if (song.ar && Array.isArray(song.ar)) {
        return song.ar.map(a=>a.name).join('/');
    }
    if (song.artist) {
        return song.artist;
    }
    if (song.artists && Array.isArray(song.artists)) {
        return song.artists.map(a=>a.name).join('/');
    }
    return '';
}
function showModal(message, type='info') {
    let color = {
        info: '#f6723a',
        success: '#388e3c',
        error: '#d32f2f',
        warning: '#fbc02d'
    }[type] || '#f6723a';
    let modal = document.createElement('div');
    modal.id = 'info-modal';
    modal.className = 'modal-anim';
    modal.innerHTML = `
      <div class="modal-backdrop" style="position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.3);z-index:9999;display:flex;align-items:center;justify-content:center;">
        <div class="modal-content modal-content-anim" style="background:#fff;border-radius:10px;box-shadow:0 2px 16px #0002;padding:18px 20px;min-width:180px;max-width:260px;position:relative;">
          <button onclick="document.body.removeChild(document.getElementById('info-modal'))" style="position:absolute;top:6px;right:8px;font-size:18px;border:none;background:none;">×</button>
          <div style="color:${color};font-size:1em;">${message}</div>
        </div>
      </div>`;
    document.body.appendChild(modal);
}
function showQrLoginModal() {
    let modal = document.createElement('div');
    modal.id = 'qr-modal';
    modal.className = 'modal-anim';
    modal.innerHTML = `
      <div class="qr-modal-bg">
        <div class="qr-modal-box modal-content-anim">
          <button onclick="document.body.removeChild(document.getElementById('qr-modal'))" style="position:absolute;top:6px;right:8px;font-size:22px;border:none;background:none;">×</button>
          <div id="qr-img-box" class="text-center mb-2"></div>
          <div id="qr-status" class="text-center text-info mb-2" style="font-size:0.98em;">请使用网易云音乐App扫码登录</div>
        </div>
      </div>`;
    document.body.appendChild(modal);
    fetch('/api/qr_key').then(r=>r.json()).then(data=>{
        let key = data.data.unikey;
        fetch(`/api/qr_create?key=${key}`).then(r=>r.json()).then(data=>{
            let qrimg = data.data.qrimg;
            document.getElementById('qr-img-box').innerHTML = `<img src='${qrimg}' style='width:140px;height:140px;'>`;
            pollQrStatus(key);
        });
    });
}
function pollQrStatus(key) {
    let statusDiv = document.getElementById('qr-status');
    let timer = setInterval(()=>{
        fetch(`/api/qr_check?key=${key}`).then(r=>r.json()).then(data=>{
            if(data.code === 800) {
                statusDiv.innerText = '二维码已过期，请关闭后重试';
                clearInterval(timer);
            } else if(data.code === 801) {
                statusDiv.innerText = '等待扫码...';
            } else if(data.code === 802) {
                statusDiv.innerText = '请在手机上确认登录';
            } else if(data.code === 803) {
                statusDiv.innerText = '登录成功！';
                setTimeout(()=>{ document.body.removeChild(document.getElementById('qr-modal')); location.reload(); }, 1000);
                clearInterval(timer);
            }
        });
    }, 2000);
}
function checkLoginStatus(cb) {
    fetch('/api/user_account').then(r=>r.json()).then(data=>{
        if(data.code === 200 && data.profile && data.profile.nickname) {
            document.getElementById('login-status').innerHTML = `<span class='text-success'>已登录：${data.profile.nickname}</span>`;
            document.getElementById('qr-login-btn').style.display = 'none';
            cb && cb(true);
        } else {
            document.getElementById('login-status').innerHTML = `<span class='text-danger'>未登录，请先 <a href='#' onclick='showQrLoginModal()'>扫码登录</a></span>`;
            document.getElementById('qr-login-btn').style.display = '';
            cb && cb(false);
        }
    });
}
// ========== 搜索功能 ==========
document.getElementById('search-form').onsubmit = function(e) {
    e.preventDefault();
    let kw = document.getElementById('search-keyword').value.trim();
    let stype = document.getElementById('search-type').value;
    if(!kw) { showModal('请输入关键词', 'warning'); return; }
    let defaultCover = 'https://via.placeholder.com/44x44?text=No+Cover';
    fetch(`/search?kw=${encodeURIComponent(kw)}&stype=${stype}`).then(r=>r.json()).then(data=>{
        let res = document.getElementById('search-result');
        res.innerHTML = '';
        if(stype==='1' && data.result && data.result.songs) {
            let ids = data.result.songs.map(song => song.id).join(',');
            fetch(`/api/song_detail?ids=${ids}`).then(r=>r.json()).then(detailData => {
                let id2cover = {};
                detailData.songs.forEach(song => {
                    id2cover[song.id] = song.al && song.al.picUrl ? song.al.picUrl : defaultCover;
                });
                // 分页
                allSongs = data.result.songs.map(song => ({...song, cover: id2cover[song.id] || defaultCover}));
                currentPage = 1;
                renderSongList();
                renderSongPagination();
            });
        } else {
            res.innerHTML = '<div class="text-danger">未找到结果</div>';
        }
    });
};
// ========== 歌单ID/链接直达 ==========
document.getElementById('fetch-btn').onclick = function() {
    let val = document.getElementById('playlist-input').value.trim();
    let pid = extractPlaylistId(val);
    if(!pid) { showModal('请输入正确的歌单ID或链接','warning'); return; }
    checkLoginStatus(function(loggedIn){
        if(!loggedIn) { showQrLoginModal(); return; }
        fetch(`/api/playlist_tracks?id=${pid}&limit=1000`)
          .then(r => r.json())
          .then(data => {
              if(!data.songs || !data.songs.length) {
                  document.getElementById('playlist-info').innerHTML = '<span class="text-danger">未获取到歌曲，可能未登录或歌单不存在</span>';
                  document.getElementById('song-list').innerHTML = '';
                  allSongs = [];
                  queue = [];
                  renderQueue();
                  renderSongPagination();
                  return;
              }
              document.getElementById('playlist-info').innerHTML = `<b>共${data.songs.length}首歌</b>`;
              allSongs = data.songs;
              currentPage = 1;
              renderSongList();
              renderQueue();
              renderSongPagination();
          });
    });
};
// ========== 歌曲分页与队列按钮 ==========
function renderSongList() {
    let html = '';
    let start = (currentPage-1)*PAGE_SIZE;
    let end = Math.min(start+PAGE_SIZE, allSongs.length);
    for(let i=start;i<end;i++){
        let song = allSongs[i];
        let cover = song.al && song.al.picUrl ? song.al.picUrl : (song.cover?song.cover:'');
        let artists = song.ar ? song.ar.map(a=>a.name).join('/') : (song.artist?song.artist:'');
        let inQueue = queue.find(q=>q.id==song.id);
        html += `<div class='song-row row align-items-center'>
            <div class='col-auto'>${cover?`<img class='cover-img' src='${cover}'>`:''}</div>
            <div class='col'>
                <b>${i+1}. ${song.name}</b><br>
                <span class='text-secondary' style='font-size:0.93em;'>${artists}</span>
            </div>
            <div class='col-auto d-flex flex-column gap-1'>
                <button class='btn btn-sm ${inQueue?'btn-danger':'btn-main'} mb-1 queue-toggle-btn' data-song='${encodeURIComponent(JSON.stringify(song))}'>${inQueue?'<i class="bi bi-x-circle btn-icon"></i>移除队列':'<i class="bi bi-plus-circle btn-icon"></i>加入队列'}</button>
                <a href="/proxy_download/${song.id}" class="btn btn-success btn-sm mb-1" download target="_blank"><i class="bi bi-download btn-icon"></i>下载</a>
                <button class="btn btn-info btn-sm" onclick='showPreviewModal(${JSON.stringify({id:song.id,name:song.name,artist:artists,cover:cover})})'><i class="bi bi-play-circle btn-icon"></i>试听</button>
            </div>
        </div>`;
    }
    document.getElementById('song-list').innerHTML = html;
    document.getElementById('add-all-btn').style.display = allSongs.length ? '' : 'none';
    document.getElementById('batch-download-btn').style.display = queue.length ? '' : 'none';
    document.getElementById('remove-all-btn').style.display = queue.length ? '' : 'none';
    // 队列按钮事件绑定
    document.querySelectorAll('.queue-toggle-btn').forEach(btn => {
        btn.onclick = function() {
            let song = JSON.parse(decodeURIComponent(this.getAttribute('data-song')));
            toggleQueue(song);
        };
    });
}
function renderSongPagination() {
    let total = Math.ceil(allSongs.length/PAGE_SIZE);
    let pag = document.getElementById('song-pagination');
    if(total<=1) { pag.innerHTML = ''; return; }
    let html = '';
    for(let i=1;i<=total;i++){
        html += `<li class="page-item${i==currentPage?' active':''}"><a class="page-link" href="#" onclick="gotoPage(${i});return false;">${i}</a></li>`;
    }
    pag.innerHTML = html;
}
function gotoPage(page) {
    currentPage = page;
    renderSongList();
    renderSongPagination();
}
function renderQueue() {
    let ql = document.getElementById('queue-list');
    ql.innerHTML = '';
    queue.forEach((song, idx) => {
        let cover = song.info && song.info.cover ? song.info.cover : '';
        let artists = song.info && song.info.artist ? song.info.artist : '';
        let html = `<div class="row align-items-center">
            <div class="col-auto">
                ${cover?`<img class='cover-img' src='${cover}'>`:''}
            </div>
            <div class="col">
                <div class="fw-bold fs-6 mb-1">${song.info.name}</div>
                <div class="text-secondary small mb-2">${artists}</div>
                <div class="d-flex gap-2 justify-content-end">
                    <button class="btn btn-info btn-sm" onclick='showPreviewModal(${JSON.stringify({id:song.id,name:song.info.name,artist:artists,cover:cover})})'><i class="bi bi-play-circle btn-icon"></i>试听</button>
                    <a href="/proxy_download/${song.id}" class="btn btn-success btn-sm" download target="_blank"><i class="bi bi-download btn-icon"></i>下载</a>
                    <button class='btn btn-sm btn-outline-danger' onclick='removeFromQueue(${idx})'><i class="bi bi-x-circle btn-icon"></i>移除</button>
                </div>
            </div>
        </div>`;
        let li = document.createElement('li');
        li.className = 'queue-item';
        li.innerHTML = html;
        ql.appendChild(li);
    });
    document.getElementById('queue-section').style.display = queue.length ? '' : 'none';
    document.getElementById('batch-download-btn').style.display = queue.length ? '' : 'none';
    document.getElementById('remove-all-btn').style.display = queue.length ? '' : 'none';
}
function addToQueue(song) {
    if(!queue.find(q=>q.id==song.id)) {
        queue.push({type:'song', id:song.id, info:{name:song.name,artist:getArtist(song),cover:(song.al && song.al.picUrl) ? song.al.picUrl : (song.cover?song.cover:'')}});
        renderSongList();
        renderQueue();
    }
}
function removeFromQueue(idx) {
    queue.splice(idx, 1);
    renderSongList();
    renderQueue();
}
function toggleQueue(song) {
    let idx = queue.findIndex(q=>q.id==song.id);
    if(idx === -1) {
        queue.push({
            type: 'song',
            id: song.id,
            info: {
                name: song.name,
                artist: getArtist(song),
                cover: (song.al && song.al.picUrl) ? song.al.picUrl : (song.cover?song.cover:'')
            }
        });
    } else {
        queue.splice(idx, 1);
    }
    renderSongList();
    renderQueue();
}
document.getElementById('add-all-btn').onclick = function() {
    let start = (currentPage-1)*PAGE_SIZE;
    let end = Math.min(start+PAGE_SIZE, allSongs.length);
    for(let i=start;i<end;i++){
        let song = allSongs[i];
        if(!queue.find(q=>q.id==song.id)) {
            queue.push({type:'song', id:song.id, info:{name:song.name,artist:getArtist(song),cover:(song.al && song.al.picUrl) ? song.al.picUrl : (song.cover?song.cover:'')}});
        }
    }
    renderSongList();
    renderQueue();
};
document.getElementById('remove-all-btn').onclick = function() {
    if(queue.length>0) {
        queue = [];
        renderSongList();
        renderQueue();
    }
};
document.getElementById('batch-download-btn').onclick = function() {
    batchSequentialDownload();
};
function batchSequentialDownload() {
    if (queue.length === 0) {
        showModal('队列为空！','warning');
        return;
    }
    let statusText = document.getElementById('batch-download-status');
    let i = 0;
    function downloadNext() {
        if (i >= queue.length) {
            statusText.innerText = '全部下载完成！';
            showModal('全部下载完成！','success');
            return;
        }
        let item = queue[i];
        statusText.innerText = `正在下载第${i+1}首：${item.info.name}`;
        let a = document.createElement('a');
        a.href = `/proxy_download/${item.id}`;
        a.download = '';
        a.target = '_blank';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        i++;
        setTimeout(downloadNext, 3000);
    }
    downloadNext();
}
// ========== 试听弹窗 ==========
function showPreviewModal(song) {
    let modal = document.getElementById('preview-modal');
    modal.innerHTML = `<div class="qr-modal-bg" style="animation:fadeIn 0.3s;">
      <div class="qr-modal-box modal-content-anim" style="min-width:340px;max-width:95vw;">
        <button onclick="document.getElementById('preview-modal').innerHTML=''" style="position:absolute;top:8px;right:12px;font-size:22px;border:none;background:none;">×</button>
        <div class="d-flex align-items-center mb-3">
          ${song.cover?`<img src='${song.cover}' style='width:80px;height:80px;border-radius:8px;border:2px solid #f6723a;object-fit:cover;'>`:''}
          <div class="ms-3">
            <b style="font-size:1.2em;">${song.name}</b><br>
            <span class="text-secondary">${song.artist}</span>
          </div>
        </div>
        <audio id="audio-preview" src="/proxy_download/${song.id}" controls style="width:100%;"></audio>
      </div>
    </div>`;
}
// ========== 扫码登录按钮 ==========
document.getElementById('qr-login-btn').onclick = function() {
    showQrLoginModal();
};
// ========== 初始化 ==========
checkLoginStatus();
</script>
</body>
</html>
'''

@app.route('/logout', methods=['POST'])
def logout():
    # 调用网易云API代理的 /logout 接口
    cookies = get_cookie()
    headers = {'Cookie': cookies} if cookies else {}
    try:
        requests.post(f'{API_BASE}/logout', headers=headers, timeout=5)
    except Exception:
        pass  # 忽略第三方API异常
    session.clear()
    session.pop('netease_user_key', None)
    return '', 204

@app.route('/debug_playlist', methods=['GET'])
def debug_playlist():
    return '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>歌单接口调试</title>
    <style>
        body { background: #f8f9fa; font-family: Arial; }
        .container { max-width: 600px; margin: 40px auto; background: #fff; border-radius: 12px; box-shadow: 0 2px 12px #0001; padding: 32px; }
        .result-area { background: #222; color: #0f0; font-family: monospace; padding: 16px; border-radius: 8px; margin-top: 18px; white-space: pre-wrap; }
        .error { color: #d32f2f; font-weight: bold; }
        .ok { color: #388e3c; font-weight: bold; }
    </style>
</head>
<body>
<div class="container">
    <h2>歌单接口调试工具</h2>
    <div>歌单ID：<input type="text" id="pid" value="" style="width:200px;"> <button onclick="debugFetch()">调试获取</button></div>
    <div id="status" style="margin:12px 0;"></div>
    <div class="result-area" id="result"></div>
</div>
<script>
function debugFetch() {
    let pid = document.getElementById('pid').value.trim();
    if(!pid) { document.getElementById('status').innerHTML = '<span class="error">请输入歌单ID</span>'; return; }
    document.getElementById('status').innerHTML = '请求中...';
    document.getElementById('result').innerText = '';
    fetch(`/api/playlist_tracks?id=${pid}&limit=1000`).then(r=>r.json()).then(data=>{
        document.getElementById('result').innerText = JSON.stringify(data, null, 2);
        if(data.songs && data.songs.length) {
            document.getElementById('status').innerHTML = `<span class='ok'>成功，返回${data.songs.length}首歌</span>`;
        } else {
            document.getElementById('status').innerHTML = `<span class='error'>无歌曲，或接口异常</span>`;
        }
    }).catch(e=>{
        document.getElementById('status').innerHTML = `<span class='error'>请求失败: ${e}</span>`;
    });
}
</script>
</body>
</html>
'''

@app.route('/API_Document.html')
def api_doc():
    return send_from_directory('.', 'API_Document.html')

if __name__ == '__main__':
    app.run(debug=True) 