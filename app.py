from flask import Flask, render_template, request, jsonify, send_file
from datetime import datetime
import pandas as pd 
import urllib.parse
import requests
import zipfile
import yt_dlp
import os
import re
import io

app = Flask(__name__)
BASE_DOWNLOAD_DIR = 'downloads'
os.makedirs(BASE_DOWNLOAD_DIR, exist_ok=True)

def sanitize_filename(name):
    # Remove or replace characters that are illegal in filenames
    return re.sub(r'[\\/:"*?<>|]+', '_', name)

@app.route('/download_template', methods=['GET'])
def download_template():
    output = io.BytesIO()
    df = pd.DataFrame(columns=['url'])
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='youtube_template.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/upload_excel', methods=['POST'])
def upload_excel():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    try:
        df = pd.read_excel(file)
        urls = df['url'].dropna().tolist()
        return jsonify({'urls': urls})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def resolve_handle_to_channel(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, timeout=10, allow_redirects=True, headers=headers)
        final_url = resp.url
        if "youtube.com/channel/" in final_url:
            return final_url
    except Exception as e:
        print("Handle resolution failed:", e)
    return None

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/fetch_video_details', methods=['GET'])
def fetch_video_details():
    video_url = request.args.get('video_url')
    if not video_url:
        return jsonify({'error': 'No video URL provided'}), 400

    if "youtube.com/@" in video_url:
        video_url = resolve_handle_to_channel(video_url) or video_url

    try:
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'noplaylist': False,
            'extract_flat': False,
            'quiet': True,
            'merge_output_format': 'mp4',
            'playlistend':5,  # Only fetch first 20 entries
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        if 'entries' in info:
            entries_data = []
            for entry in info['entries']:
                # if entry.get('duration') and entry['duration'] <= 120:  # Only Shorts of less than 2minutes
                if entry.get('duration'):  # Only Shorts
                    entries_data.append({
                        'title': entry.get('title'),
                        'url': f"https://www.youtube.com/watch?v={entry.get('id')}",
                        'thumbnail_url': entry.get('thumbnail'),
                        'length': entry.get('duration'),
                        'channel': entry.get('uploader'),
                    })
            return jsonify({'type': 'playlist', 'entries': entries_data})

        video_info = {
            'title': info.get('title'),
            'thumbnail_url': info.get('thumbnail'),
            'length': info.get('duration'),
            'views': info.get('view_count'),
            'description': info.get('description'),
        }

        video_streams = [
            {
                'resolution': f"{f.get('width')}x{f.get('height')}" if f.get('width') else f.get('format_note', 'Unknown'),
                'filesize': f"{round(f.get('filesize', 0) / 1024 / 1024, 2)} MB" if f.get('filesize') else 'Unknown',
                'format_id': f.get('format_id'),
                'has_audio': f.get('acodec') != 'none'
            }
            for f in info.get('formats', [])
            if f.get('vcodec') != 'none'
        ]

        audio_streams = [
            {
                'abr': f.get('abr'),
                'filesize': f"{round(f.get('filesize', 0) / 1024 / 1024, 2)} MB" if f.get('filesize') else 'Unknown',
                'format_id': f.get('format_id')
            }
            for f in info.get('formats', [])
            if f.get('acodec') != 'none' and f.get('vcodec') == 'none'
        ]

        return jsonify({
            'type': 'single',
            'video_info': video_info,
            'video_streams': video_streams,
            'audio_streams': audio_streams
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download_media', methods=['GET'])
def download_media():
    video_url = request.args.get('video_url')
    if not video_url:
        return jsonify({'error': 'Missing video URL'}), 400

    try:
        # Folder structure
        today_str = datetime.now().strftime('%Y-%m-%d')
        target_folder = os.path.join(BASE_DOWNLOAD_DIR, today_str, 'Original Language')
        os.makedirs(target_folder, exist_ok=True)

        english_folder = os.path.join(BASE_DOWNLOAD_DIR, today_str, 'English Language')
        os.makedirs(english_folder, exist_ok=True)

        # Temporary metadata fetch to extract title & channel
        probe_opts = {
            'quiet': True,
            'skip_download': True,
        }
        with yt_dlp.YoutubeDL(probe_opts) as probe_ydl:
            info = probe_ydl.extract_info(video_url, download=False)

        title = sanitize_filename(info.get('title', 'video'))
        channel = sanitize_filename(info.get('uploader', 'channel'))

        output_filename = f"{channel} | {title}.%(ext)s"
        output_template = os.path.join(target_folder, output_filename)

        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'outtmpl': output_template,
            'quiet': True,
            'noplaylist': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            filename = ydl.prepare_filename(info)
            if not filename.endswith('.mp4'):
                filename = filename.rsplit('.', 1)[0] + '.mp4'

        return send_file(
            filename,
            as_attachment=True,
            download_name=os.path.basename(filename)
        )

    except Exception as e:
        print("Download error:", str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/bulk_download', methods=['POST'])
def bulk_download():
    data = request.get_json()
    video_urls = data.get('urls', [])

    print("Received bulk download URLs:", video_urls)

    if not video_urls:
        return jsonify({'error': 'No video URLs provided'}), 400

    try:
        today_str = datetime.now().strftime('%Y-%m-%d')
        target_folder = os.path.join(BASE_DOWNLOAD_DIR, today_str, 'Original Language')
        os.makedirs(target_folder, exist_ok=True)

        english_folder = os.path.join(BASE_DOWNLOAD_DIR, today_str, 'English Language')
        os.makedirs(english_folder, exist_ok=True)
        
        filepaths = []

        for url in video_urls:
            try:
                # Convert Shorts URL to standard watch URL
                if "youtube.com/shorts/" in url:
                    url = url.replace("youtube.com/shorts/", "youtube.com/watch?v=")

                probe_opts = {'quiet': True, 'skip_download': True}
                with yt_dlp.YoutubeDL(probe_opts) as probe_ydl:
                    info = probe_ydl.extract_info(url, download=False)

                title = sanitize_filename(info.get('title', 'video'))
                channel = sanitize_filename(info.get('uploader', 'channel'))

                output_filename = f"{channel} | {title}.%(ext)s"
                output_template = os.path.join(target_folder, output_filename)

                ydl_opts = {
                    'format': 'bestvideo+bestaudio/best',
                    'merge_output_format': 'mp4',
                    'outtmpl': output_template,
                    'quiet': True,
                    'noplaylist': True,
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    final_path = ydl.prepare_filename(info)
                    if not final_path.endswith('.mp4'):
                        final_path = final_path.rsplit('.', 1)[0] + '.mp4'

                    # Check if file actually exists and has size
                    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
                        filepaths.append(final_path)
                    else:
                        print(f"Warning: File {final_path} was empty or missing.")
            except Exception as e:
                print(f"Failed to process {url}: {e}")

        if not filepaths:
            return jsonify({'error': 'No videos were successfully downloaded'}), 500

        # Generate URLs for UI download
        file_urls = [f"/download_file?path={urllib.parse.quote(path)}" for path in filepaths]
        return jsonify({'file_urls': file_urls})
        # return send_file(memory_zip, mimetype='application/zip', as_attachment=True, download_name='shorts_bulk.zip')

    except Exception as e:
        print("Bulk download error:", str(e))
        return jsonify({'error': str(e)}), 500
    
if __name__ == '__main__':
    app.run(debug=True)
