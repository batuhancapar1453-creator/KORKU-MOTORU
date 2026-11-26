from __future__ import annotations
import argparse, asyncio, concurrent.futures, hashlib, json, math, os, random, shutil, subprocess, sys, time, wave
from pathlib import Path
from typing import Any
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output'
DATA = ROOT / 'data'
STORY_DIR = ROOT / 'korku_motoru' / 'stories'
W, H, FPS = 1080, 1920, 24


def log(msg: str): print(f'[KORKU MOTORU] {msg}', flush=True)

def run(cmd: list[str], **kwargs):
    subprocess.run(cmd, check=True, **kwargs)

def load_story(path: Path) -> dict[str, Any]:
    data=json.loads(path.read_text(encoding='utf-8'))
    if len(data.get('scenes', [])) != 45: raise ValueError('Hikâye tam olarak 45 sahne içermeli.')
    if data.get('duration_seconds') != 180: raise ValueError('Video süresi 180 saniye olmalı.')
    for i,s in enumerate(data['scenes'],1):
        if s.get('n') != i or not s.get('prompt') or not s.get('narration'):
            raise ValueError(f'Sahne {i} eksik veya sıra numarası hatalı.')
    return data

def cloudflare_image(prompt: str, dest: Path, scene_no: int):
    account=os.getenv('CLOUDFLARE_ACCOUNT_ID','').strip(); token=os.getenv('CLOUDFLARE_API_TOKEN','').strip()
    if not account or not token: raise RuntimeError('CLOUDFLARE_ACCOUNT_ID ve CLOUDFLARE_API_TOKEN GitHub Secrets olarak gerekli.')
    model=os.getenv('CF_IMAGE_MODEL','@cf/black-forest-labs/flux-2-klein-4b')
    style=('Professional hand-drawn 2D cinematic horror animation still, vertical portrait composition, expressive illustrated character, clean slightly imperfect ink contours, painted shading, muted navy blue and violet palette, atmospheric shadows, no photorealism, no 3D, no anime, no text. '
           'Main character is BATU: same 24-year-old young man, short messy dark brown hair, hazel-brown eyes, charcoal gray sweatshirt, navy sweatpants. '
           'Same small bedroom: bed, bedside table on the left, black charging cable, small lamp, rainy window on the right, digital clock, door opposite bed. '
           'Maintain character and room continuity. Scene description: ' + prompt)
    url=f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}'
    r=requests.post(url,headers={'Authorization':f'Bearer {token}','Content-Type':'application/json','Accept':'image/png'},json={'prompt':style},timeout=180)
    if not r.ok: raise RuntimeError(f'Cloudflare image API sahne {scene_no}: HTTP {r.status_code} {r.text[:300]}')
    ctype=r.headers.get('content-type','')
    if 'application/json' in ctype:
        payload=r.json()
        if not payload.get('success'): raise RuntimeError(f'Cloudflare image API hata: {payload}')
        import base64
        raw=payload.get('result',{}).get('image')
        if not raw: raise RuntimeError('Cloudflare yanıtında görsel bulunamadı.')
        dest.write_bytes(base64.b64decode(raw))
    else: dest.write_bytes(r.content)
    with Image.open(dest) as im:
        im.verify()
    return dest

def placeholder_image(scene: dict, dest: Path):
    # Local smoke-test fallback only; never used in a production run.
    im=Image.new('RGB',(768,1365),(12,18,32)); d=ImageDraw.Draw(im)
    d.ellipse((240,260,530,550),fill=(90,86,105),outline=(190,180,190),width=5)
    d.rectangle((180,520,590,1120),fill=(62,65,78),outline=(180,180,190),width=5)
    d.rectangle((500,80,700,220),fill=(20,30,45),outline=(120,150,180),width=3)
    d.text((35,30),f"SCENE {scene['n']:02d}",(230,230,240))
    d.text((35,1180),scene.get('screen','')[:40],fill=(235,235,245))
    im.save(dest)

def generate_images(story: dict, test_mode=False):
    folder=OUT/'images'; folder.mkdir(parents=True,exist_ok=True)
    todo=[]
    for s in story['scenes']:
        p=folder/f"scene_{s['n']:02d}.png"
        if p.exists() and p.stat().st_size>1000: continue
        todo.append((s,p))
    if test_mode:
        for s,p in todo: placeholder_image(s,p)
        return
    workers=max(1,min(int(os.getenv('IMAGE_WORKERS','4')),8))
    log(f'{len(todo)} sahne görseli üretilecek; eşzamanlı istek: {workers}')
    errors=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(cloudflare_image,s['prompt'],p,s['n']):(s,p) for s,p in todo}
        for f in concurrent.futures.as_completed(futures):
            s,p=futures[f]
            try: f.result(); log(f"Görsel hazır: {s['n']}/45")
            except Exception as e: errors.append((s['n'],str(e)))
    if errors: raise RuntimeError('Görsel üretimi tamamlanamadı: '+'; '.join(f'{n}: {e}' for n,e in errors[:8]))

def add_screen_text(image_path: Path, text: str):
    if not text: return
    im=Image.open(image_path).convert('RGB'); d=ImageDraw.Draw(im)
    # Editorial overlay approximates a phone UI when no exact phone screen is visible.
    try: font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',28)
    except Exception: font=ImageFont.load_default()
    # Keep screen copy as a discreet lower-third to avoid corrupting generated phone text.
    box=(45, im.height-205, im.width-45, im.height-65)
    d.rounded_rectangle(box,radius=22,fill=(8,12,22),outline=(115,135,165),width=2)
    d.text((70,im.height-165),text[:90],font=font,fill=(245,245,250))
    im.save(image_path)

def make_clip(image_path: Path, dest: Path, idx: int):
    # Free-mode animation: cinematic 2D camera motion over AI-generated scene art.
    direction='in' if idx%2==0 else 'out'
    if direction=='in': z="min(zoom+0.00055,1.052)"
    else: z="if(eq(on,1),1.052,max(zoom-0.00055,1.0))"
    vf=f"scale=1200:2134:force_original_aspect_ratio=increase,crop=1200:2134,zoompan=z='{z}':d={FPS*4}:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p"
    run(['ffmpeg','-hide_banner','-loglevel','error','-y','-loop','1','-i',str(image_path),'-vf',vf,'-t','4','-r',str(FPS),'-an','-c:v','libx264','-preset','ultrafast','-crf','24',str(dest)])

def make_music(path: Path, duration=180, seed='video_001'):
    import numpy as np
    sr=22050; n=sr*duration; t=np.arange(n,dtype=np.float32)/sr
    seedv=int(hashlib.sha256(seed.encode()).hexdigest()[:8],16); rng=np.random.default_rng(seedv)
    freqs=rng.choice([43.65,55.0,65.41,73.42,82.41],size=4,replace=False)
    music=np.zeros(n,dtype=np.float32)
    for i,f in enumerate(freqs):
        phase=float(rng.random()*6.28); rate=float(rng.uniform(0.015,0.04))
        music += (0.10/(i+1))*np.sin(2*np.pi*f*t + phase + np.sin(2*np.pi*rate*t)*0.6)
    # Slow pulsing tension, generated uniquely from the episode ID.
    pulse=(0.55+0.45*np.sin(2*np.pi*0.11*t))
    noise=rng.normal(0,1,n).astype(np.float32)
    # Smooth noise into a very soft atmospheric layer.
    kernel=np.ones(700,dtype=np.float32)/700
    ambience=np.convolve(noise,kernel,mode='same')
    music=(music*pulse + ambience*0.035).astype(np.float32)
    fade=int(sr*3); music[:fade]*=np.linspace(0,1,fade); music[-fade:]*=np.linspace(1,0,fade)
    music=np.clip(music,-0.45,0.45)
    stereo=np.stack([music,music],axis=1)
    with wave.open(str(path),'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((stereo*32767).astype('<i2').tobytes())

def make_sfx(path: Path, duration=180):
    sr=22050; n=sr*duration; t=[i/sr for i in range(n)]; samples=[0.0]*n
    # Restrained, synthetic phone notification/vibration cues.
    for sec,freq in [(4.2,740),(16.1,660),(48.2,740),(52.1,520),(84.2,740),(96.2,520),(140.1,740),(164.2,660),(176.1,740)]:
        start=int(sec*sr); length=int(.16*sr)
        for j in range(length):
            if start+j<n: samples[start+j]+=0.12*math.sin(2*math.pi*freq*j/sr)*math.exp(-j/(sr*.05))
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b''.join(int(max(-1,min(1,x))*32767).to_bytes(2,'little',signed=True) for x in samples))

async def make_voice(text: str, path: Path):
    import edge_tts
    voice=os.getenv('TTS_VOICE','tr-TR-AhmetNeural')
    communicate=edge_tts.Communicate(text,voice,rate='-3%',pitch='-5Hz')
    await communicate.save(str(path))

def make_srt(story, path: Path):
    # Narration text captions are timed evenly by word count across the film.
    words=[]
    for s in story['scenes']: words.extend(s['narration'].split())
    chunks=[' '.join(words[i:i+7]) for i in range(0,len(words),7)]
    total=max(1,len(chunks));
    def ts(sec):
        ms=int(sec*1000); h=ms//3600000; ms%=3600000; m=ms//60000; ms%=60000; s=ms//1000; ms%=1000
        return f'{h:02}:{m:02}:{s:02},{ms:03}'
    lines=[]
    for i,c in enumerate(chunks):
        a=180*i/total; b=180*(i+1)/total
        lines += [str(i+1),f'{ts(a)} --> {ts(b)}',c,'']
    path.write_text('\n'.join(lines),encoding='utf-8')

def assemble(story, test_mode=False):
    clips=OUT/'clips'; clips.mkdir(parents=True,exist_ok=True)
    for s in story['scenes']:
        img=OUT/'images'/f"scene_{s['n']:02d}.png"
        if s.get('screen') and not test_mode:
            display_img=OUT/'images'/f"scene_{s['n']:02d}_display.png"
            if not display_img.exists() or display_img.stat().st_mtime < img.stat().st_mtime:
                shutil.copy2(img,display_img); add_screen_text(display_img,s['screen'])
            img_for_clip=display_img
        else: img_for_clip=img
        dest=clips/f"clip_{s['n']:02d}.mp4"
        if not dest.exists() or dest.stat().st_size<1000:
            make_clip(img_for_clip,dest,s['n'])
        log(f"Klip hazır: {s['n']}/45")
    concat=OUT/'concat.txt'
    concat.write_text('\n'.join(f"file '{(clips/f'clip_{s['n']:02d}.mp4').as_posix()}'" for s in story['scenes']),encoding='utf-8')
    silent=OUT/'silent.mp4'
    run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(silent)])
    music=OUT/'music_unique.wav'; sfx=OUT/'sfx.wav'; voice=OUT/'narration.mp3'
    make_music(music,180,story['id']); make_sfx(sfx,180)
    narration=' '.join(s['narration'] for s in story['scenes'])
    asyncio.run(make_voice(narration,voice))
    make_srt(story,OUT/'captions.srt')
    final=OUT/'KORKU_MOTORU_BATU_VIDEO_001.mp4'
    run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(silent),'-i',str(voice),'-i',str(music),'-i',str(sfx),'-filter_complex','[1:a]apad=whole_dur=180,atrim=0:180,volume=1.0[n];[2:a]volume=0.18[m];[3:a]volume=0.35[s];[m][s]amix=inputs=2:duration=longest[bg];[n][bg]amix=inputs=2:duration=longest:dropout_transition=2[a]','-map','0:v:0','-map','[a]','-t','180','-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',str(final)])
    probe=subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration:stream=width,height','-of','json',str(final)],text=True)
    meta=json.loads(probe); duration=float(meta['format']['duration']); video=next(x for x in meta['streams'] if x.get('codec_type')=='video') if any(x.get('codec_type')=='video' for x in meta['streams']) else meta['streams'][0]
    if abs(duration-180)>1.0: raise RuntimeError(f'Son video süresi 180 saniye değil: {duration}')
    if video.get('width')!=W or video.get('height')!=H: raise RuntimeError('Son video çözünürlük kontrolünden geçemedi.')
    log(f'Video kalite kontrolü başarılı: {duration:.2f}s, {W}x{H}')
    return final

def upload_youtube(video: Path, story: dict):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    cid=os.getenv('YOUTUBE_CLIENT_ID',''); secret=os.getenv('YOUTUBE_CLIENT_SECRET',''); refresh=os.getenv('YOUTUBE_REFRESH_TOKEN','')
    if not all([cid,secret,refresh]): raise RuntimeError('YouTube OAuth secrets eksik.')
    creds=Credentials(None,refresh_token=refresh,token_uri='https://oauth2.googleapis.com/token',client_id=cid,client_secret=secret,scopes=['https://www.googleapis.com/auth/youtube.upload'])
    creds.refresh(Request())
    yt=build('youtube','v3',credentials=creds)
    body={'snippet':{'title':'Neden Hâlâ Uyanıksın? | 02:43\'te Gelen Mesaj','description':story['description']+'\n\n#Korku #Gizem #Shorts','tags':['Korku','Gizem','Korku Hikayesi','Animasyon','Shorts'],'categoryId':'24'},'status':{'privacyStatus':os.getenv('YOUTUBE_PRIVACY','public'),'selfDeclaredMadeForKids':False}}
    req=yt.videos().insert(part='snippet,status',body=body,media_body=MediaFileUpload(str(video),mimetype='video/mp4',resumable=True,chunksize=8*1024*1024))
    response=None
    while response is None:
        _,response=req.next_chunk()
    log('YouTube yükleme tamamlandı: '+response.get('id','id yok'))
    return response.get('id')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--self-test',action='store_true'); ap.add_argument('--no-upload',action='store_true'); ap.add_argument('--story',default='video_001.json'); args=ap.parse_args()
    OUT.mkdir(exist_ok=True); DATA.mkdir(exist_ok=True)
    story=load_story(STORY_DIR/args.story)
    history_path=DATA/'history.json'; history=json.loads(history_path.read_text()) if history_path.exists() else {'published':[]}
    if not args.self_test and any(x.get('id')==story['id'] for x in history['published']):
        log('Bu bölüm daha önce yayınlanmış; çift yükleme engellendi.'); return
    generate_images(story,args.self_test)
    final=assemble(story,args.self_test)
    if not args.self_test and not args.no_upload:
        video_id=upload_youtube(final,story)
        history['published'].append({'id':story['id'],'video_id':video_id,'title':story['title'],'uploaded_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
        history_path.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
    log('İşlem başarıyla tamamlandı: '+str(final))

if __name__=='__main__':
    try: main()
    except Exception as e:
        log('HATA: '+str(e)); sys.exit(1)
