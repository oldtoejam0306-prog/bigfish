"""Local storyboard -> reviewed media -> existing upload-draft payload.

Independent stdlib implementation invoking an already installed ffmpeg/ffprobe.
No model, network, speech synthesis, login, or publishing. FFmpeg is not bundled.
Subtitles are UTF-8 SRT plus a default mov_text track, not burned-in typography;
players must support/enable subtitles. Windows/macOS execution remains unverified.
"""
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time

from .runtime import Fault, canonical
from .upload import inspect_video

_ID = re.compile(r'^[0-9a-f]{24}$')
_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')


def _text(value, maximum, label):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > maximum:
        raise Fault(label + '无效或过长')
    if any(ord(char) < 32 and char not in '\n\t' for char in value):
        raise Fault(label + '含控制字符')
    return value.strip()


def _stamp(seconds):
    milliseconds = round(seconds * 1000)
    return f'{milliseconds // 3600000:02}:{milliseconds // 60000 % 60:02}:{milliseconds // 1000 % 60:02},{milliseconds % 1000:03}'


class CreationStudio:
    """Only explicit public-use scripts/assets enter this independent directory."""
    def __init__(self, root):
        self.root = (Path(root).expanduser().resolve() / 'creations')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise Fault('制作目录不可为符号链接')
        self.lock = threading.RLock()

    def _job(self, ident):
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise Fault('制作任务ID无效')
        path = self.root / ident
        if not path.is_dir() or path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise Fault('制作任务不存在')
        return path

    def _read(self, ident):
        path = self._job(ident) / 'plan.json'
        if path.is_symlink() or path.stat().st_size > 200000:
            raise Fault('制作计划无效')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise Fault('无法读取制作计划') from None
        return data

    def _write(self, job, data):
        temp = job / 'plan.tmp'
        with temp.open('w', encoding='utf-8') as stream:
            stream.write(canonical(data) + '\n')
        os.replace(temp, job / 'plan.json')

    def plan(self, script):
        if not isinstance(script, dict) or set(script) - {'title', 'description', 'scenes', 'public_use_confirmed', 'size'}:
            raise Fault('脚本字段无效')
        if script.get('public_use_confirmed') is not True:
            raise Fault('必须由主人明确确认脚本/素材可公开使用；不自动读取聊天或私密素材')
        title = _text(script.get('title'), 240, '标题')
        if len(title) > 80:
            raise Fault('标题最多80字符')
        description = _text(script.get('description', '独立AI角色创作；本地制作，尚未发布。'), 3000, '简介')
        if len(description)>1000:
            raise Fault('简介最多1000字符，以便交接现有投稿草稿')
        size = script.get('size', '640x360')
        if size not in ('640x360', '1280x720'):
            raise Fault('仅支持640x360或1280x720')
        scenes = script.get('scenes')
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 20:
            raise Fault('需1至20个分镜')
        validated = []
        total = 0
        # Validate every source before creating the job directory.
        for index, scene in enumerate(scenes):
            if not isinstance(scene, dict) or set(scene) - {'text', 'duration', 'color', 'image', 'source'}:
                raise Fault('分镜字段无效')
            duration = scene.get('duration', 5)
            if type(duration) is not int or not 1 <= duration <= 30:
                raise Fault('单镜时长须为1至30整数秒')
            total += duration
            if total > 120:
                raise Fault('总时长最多120秒')
            text = _text(scene.get('text'), 3000, '字幕')
            source = _text(scene.get('source'), 2000, '素材出处/授权说明')
            value = {'text': text, 'duration': duration, 'source': source}
            if 'image' in scene:
                if 'color' in scene:
                    raise Fault('单镜只能选择颜色或图片')
                if not isinstance(scene['image'], str):
                    raise Fault('图片须为明确选择的本地路径')
                origin = Path(scene['image']).expanduser()
                if origin.is_symlink():
                    raise Fault('图片不能是符号链接')
                origin = origin.resolve()
                if not origin.is_file() or not 0 < origin.stat().st_size <= 20000000:
                    raise Fault('图片不存在或超过20MB')
                with origin.open('rb') as stream:
                    raw = stream.read(20000001)
                if len(raw) > 20000000:
                    raise Fault('图片超过20MB')
                if raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    suffix = '.png'
                elif raw.startswith(b'\xff\xd8\xff'):
                    suffix = '.jpg'
                elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
                    suffix = '.webp'
                else:
                    raise Fault('只接受PNG/JPEG/WebP容器，不接受SVG、播放列表或远程URL')
                value.update(asset=f'scene-{index:02}{suffix}', asset_sha256=hashlib.sha256(raw).hexdigest(), _bytes=raw)
            else:
                color = scene.get('color', '#203040')
                if not isinstance(color, str) or not _COLOR.fullmatch(color):
                    raise Fault('颜色须为#RRGGBB，不能包含ffmpeg表达式')
                value['color'] = color.lower()
            validated.append(value)
        ident = secrets.token_hex(12)
        job = self.root / ident
        job.mkdir(mode=0o700)
        for scene in validated:
            raw = scene.pop('_bytes', None)
            if raw is not None:
                (job / scene['asset']).write_bytes(raw)
        data = {'id': ident, 'version': 1, 'state': 'planned', 'title': title, 'description': description,
                'size': size, 'duration': total, 'scenes': validated, 'public_use_confirmed': True,
                'ai_identity': '大肥鱼AI虚拟角色创作', 'audio': 'none', 'subtitles': 'default mov_text track plus external SRT',
                'published': False}
        self._write(job, data)
        return self.preview(ident)

    def preview(self, ident):
        data = self._read(ident)
        return dict(data, requires_render_confirmation=True, network_called=False, dependency_status={
            'ffmpeg': bool(shutil.which('ffmpeg')), 'ffprobe': bool(shutil.which('ffprobe'))},
            note='本地分镜预览；不是已渲染视频或已发布内容。字幕为默认软字幕轨，非烧录字体。')

    def _run(self, args, job, cancel_check, timeout=90):
        if cancel_check and cancel_check():
            raise Fault('本地制作已取消')
        # Error output is temporary, never returned or written to user logs.
        with tempfile.TemporaryFile() as error_stream:
            try:
                process = subprocess.Popen(args, cwd=str(job), stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=error_stream)
            except OSError:
                raise Fault('无法启动可信本地ffmpeg') from None
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if cancel_check and cancel_check():
                        raise Fault('本地制作已取消')
                    if time.monotonic() - started > timeout:
                        raise Fault('本地制作超时')
                    time.sleep(0.05)
                if process.returncode != 0:
                    raise Fault('本地编码失败；请检查可信ffmpeg组件与素材格式')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=3)

    def render(self, ident, confirmation, cancel_check=None):
        if confirmation != '确认制作公开素材':
            raise Fault('请先预览并明确确认制作公开素材')
        job = self._job(ident)
        lockfile = job / 'render.lock'
        try:
            descriptor = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise Fault('已有制作进程或中断记录，请人工核对；不抢占或重复制作') from None
        os.close(descriptor)
        try:
            with self.lock:
                data = self._read(ident)
                if data['state'] == 'rendered':
                    return self.inspect(ident)
                if data['state'] not in ('planned', 'failed', 'cancelled'):
                    raise Fault('制作任务状态需人工核对')
                if cancel_check and cancel_check():
                    data['state'] = 'cancelled'; self._write(job, data)
                    raise Fault('创作任务已取消')
                ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
                if not ffmpeg or not ffprobe:
                    raise Fault('缺少可信已安装ffmpeg/ffprobe，未安装或下载任何组件')
                if data.get('public_use_confirmed') is not True or data.get('size') not in ('640x360','1280x720'):
                    raise Fault('制作计划的公开授权/画幅无效')
                if not isinstance(data.get('scenes'),list) or not 1<=len(data['scenes'])<=20:
                    raise Fault('制作计划分镜数无效')
                if any(not isinstance(x,dict) or type(x.get('duration')) is not int or not 1<=x['duration']<=30 for x in data['scenes']):
                    raise Fault('制作计划时长无效')
                total=sum(x['duration'] for x in data['scenes'])
                if total>120 or data.get('duration')!=total:
                    raise Fault('制作计划总时长无效')
                if any(path.is_symlink() or (path.is_file() and path.stat().st_nlink!=1) for path in job.iterdir()):
                    raise Fault('制作目录含链接文件，拒绝覆盖')
                # Do not trust mutated on-disk asset names as arbitrary paths.
                for scene in data['scenes']:
                    if 'asset' in scene:
                        asset = scene['asset']
                        if not re.fullmatch(r'scene-[0-9]{2}\.(png|jpg|webp)', asset):
                            raise Fault('素材引用无效')
                        file = job / asset
                        if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != scene['asset_sha256']:
                            raise Fault('已预览素材发生改变，拒绝制作')
                data['state'] = 'rendering'; self._write(job, data)
                try:
                    width, height = map(int, data['size'].split('x'))
                    captions = []; cursor = 0
                    for index, scene in enumerate(data['scenes']):
                        # Validation also protects an edited plan before invocation.
                        duration = scene['duration']
                        if type(duration) is not int or not 1 <= duration <= 30:
                            raise Fault('分镜时长无效')
                        if 'asset' in scene:
                            args = [ffmpeg, '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                                    '-f', 'image2', '-pattern_type', 'none', '-loop', '1', '-i', scene['asset'],
                                    '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2']
                        else:
                            if not _COLOR.fullmatch(scene['color']):
                                raise Fault('颜色无效')
                            args = [ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                    f"color=c=0x{scene['color'][1:]}:s={width}x{height}:r=24:d={duration}"]
                        args += ['-t', str(duration), '-r', '24', '-an', '-c:v', 'libx264', '-preset', 'veryfast',
                                 '-threads', '2', '-pix_fmt', 'yuv420p', f'clip-{index:02}.mp4']
                        self._run(args, job, cancel_check)
                        # One cue per scene. Newlines cannot create fake SRT blocks.
                        plain = ' '.join(_text(scene['text'], 3000, '字幕').split())
                        if index==0:plain='【AI·大肥鱼】 '+plain
                        captions.append(f'{index+1}\n{_stamp(cursor)} --> {_stamp(cursor+duration)}\n{html.escape(plain)}\n')
                        cursor += duration
                    (job / 'captions.srt').write_text('\n'.join(captions), encoding='utf-8')
                    (job / 'clips.txt').write_text(''.join(f"file 'clip-{index:02}.mp4'\n" for index in range(len(data['scenes']))), encoding='utf-8')
                    self._run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-f', 'concat', '-safe', '1',
                               '-i', 'clips.txt', '-f', 'srt', '-i', 'captions.srt', '-map', '0:v:0', '-map', '1:0',
                               '-c:v', 'copy', '-c:s', 'mov_text', '-disposition:s:0', 'default', '-metadata:s:s:0', 'language=zho',
                               '-metadata', 'comment=AI character creation; not published', '-movflags', '+faststart', 'video.partial.mp4'], job, cancel_check)
                    if cancel_check and cancel_check():
                        raise Fault('本地制作已取消')
                    try:
                        probe = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries',
                                                'format=duration:stream=codec_type,codec_name,width,height', '-of', 'json', 'video.partial.mp4'],
                                               cwd=str(job), capture_output=True, timeout=20, check=True)
                        media = json.loads(probe.stdout)
                        duration = float(media['format']['duration'])
                        streams = media['streams']
                        if not math.isfinite(duration) or not data['duration'] - .4 <= duration <= data['duration'] + .8:
                            raise ValueError('duration')
                        if not any(s.get('codec_type') == 'video' and s.get('codec_name') == 'h264' and s.get('width') == width and s.get('height') == height for s in streams):
                            raise ValueError('video')
                        if not any(s.get('codec_type') == 'subtitle' and s.get('codec_name') == 'mov_text' for s in streams):
                            raise ValueError('subtitle')
                    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                        raise Fault('生成媒体校验失败，不交给投稿草稿') from None
                    os.replace(job / 'video.partial.mp4', job / 'video.mp4')
                    inspected = inspect_video(job / 'video.mp4')
                    data.update(state='rendered', output={'filename': 'video.mp4', 'bytes': inspected['file_bytes'], 'sha256': inspected['file_sha256'], 'duration': duration})
                    self._write(job, data)
                    manifest = {'renderer':'external ffmpeg CLI; not bundled; installed executable license applies','ai_identity': data['ai_identity'], 'title': data['title'], 'output': data['output'],
                                'source_credits': [{'scene': index+1, 'source': scene['source'], 'asset_sha256': scene.get('asset_sha256')} for index, scene in enumerate(data['scenes'])],
                                'audio': 'none', 'subtitles': data['subtitles'], 'published': False}
                    (job / 'sources.json').write_text(canonical(manifest) + '\n', encoding='utf-8')
                except Exception as exc:
                    data['state'] = 'cancelled' if cancel_check and cancel_check() else 'failed'
                    self._write(job, data)
                    (job / 'video.partial.mp4').unlink(missing_ok=True)
                    if isinstance(exc, Fault):
                        raise
                    raise Fault('本地制作失败，未上传或发布') from None
                return self.inspect(ident)
        finally:
            lockfile.unlink(missing_ok=True)

    def inspect(self, ident):
        data = self._read(ident)
        if data['state'] != 'rendered':
            raise Fault('视频尚未完成制作')
        job=self._job(ident)
        if (job/'video.mp4').is_symlink() or (job/'video.mp4').resolve().parent!=job or (job/'sources.json').is_symlink():
            raise Fault('输出引用已改变，禁止交接')
        inspected = inspect_video(job / 'video.mp4')
        if inspected['file_sha256'] != data['output']['sha256'] or inspected['file_bytes'] != data['output']['bytes']:
            raise Fault('制作输出已改变，禁止交接投稿')
        return dict(inspected, id=ident, state='rendered', published=False, audio='none',
                    subtitles='default mov_text; external captions.srt', sources_manifest=str(self._job(ident) / 'sources.json'))

    def upload_payload(self, ident, *, tid, tags, copyright, source=''):
        """Returns fields accepted by Workbench.draft('video', payload), never sends."""
        output = self.inspect(ident); data = self._read(ident)
        if type(tid) is not int or not 1 <= tid <= 1000:
            raise Fault('分区无效')
        tags = _text(tags, 200, '标签')
        if type(copyright) is not int or copyright not in (1, 2):
            raise Fault('版权声明无效')
        if copyright == 2 and (not isinstance(source, str) or not source.startswith('https://')):
            raise Fault('转载需明确HTTPS来源')
        return {'file': output['file'], 'title': data['title'], 'text': data['description'], 'tid': tid,
                'tags': tags, 'copyright': copyright, 'source': source}
