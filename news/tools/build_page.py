#!/usr/bin/env python3
"""朝のニュース（読む）と音声（聞く）を1ページにまとめる。

usage: python3 build_page.py magazine.html video.json data.json OUT.html
  magazine.html : ページの雛形（tools/magazine.html。旧来の朝のニュースHTMLも可）
  video.json : make_news_video.py が出力した章と時刻
  data.json  : 当日の朝のニュースのJSON（news-data を差し替える）
"""
import json, re, sys

src, vj, dj, out = sys.argv[1:5]
h = open(src, encoding="utf-8").read()
V = json.load(open(vj, encoding="utf-8"))
D = json.load(open(dj, encoding="utf-8"))

def esc(s):
    return s.replace("</", "<\\/")

mins = max(1, round(V.get("duration", 0) / 60))
if "__NEWS_DATA__" in h:
    # 雑誌風テンプレート（tools/magazine.html）
    h = h.replace("__NEWS_DATA__", esc(json.dumps(D, ensure_ascii=False)), 1)
    h = h.replace('<span id="lsn-sub">画面を消しても再生が続きます</span>',
                  f'<span id="lsn-sub">約{mins}分・画面を消しても続きます</span>', 1)
    m = None
else:
    # 旧：朝のニュースのアーティファクトHTMLを流用
    h = re.sub(r'(<script type="application/json" id="news-data">)(.*?)(</script>)',
               lambda m: m.group(1) + esc(json.dumps(D, ensure_ascii=False)) + m.group(3), h, count=1, flags=re.S)
    h = h.replace("<head>", '<head><meta name="apple-mobile-web-app-capable" content="yes">'
                  '<link rel="apple-touch-icon" href="art/brief.png">', 1)
    h = re.sub(r'<section class="lsn" id="lsn".*?</section>',
        '<section class="lsn" id="lsn" aria-label="音声で聞く"><div class="in">'
        f'<div class="lt"><b>歩きながら聞く</b><span>Geminiの声で約{mins}分。画面を消しても再生が続きます</span><span id="lsn-meta" hidden></span></div>'
        '<button type="button" class="go" id="lsn-go"><svg viewBox="0 0 16 16" aria-hidden="true">'
        '<path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg><span>聞く</span></button>'
        '</div></section>', h, count=1, flags=re.S)
    h = re.sub(r'<div class="pl-note" id="pl-note">.*?</div>',
               '<div class="pl-note" id="pl-note">画面を消しても続きます。'
               '<a href="video.html" style="color:inherit">テロップ画面で見る</a></div>', h, count=1, flags=re.S)
    speech = [x for x in re.finditer(r"<script>\n\(function\(\)\{\n  var D=JSON.parse.*?</script>", h, flags=re.S)
              if "speechSynthesis" in x.group(0)]
    assert speech, "読み上げスクリプトが見つかりません"
    m = speech[-1]
chap = [{"sec": c.get("sec") or "", "secName": c.get("secName") or "", "title": c.get("title") or "",
         "start": c.get("start", 0)} for c in V["chapters"]]
PLAYER = r"""<script>
(function(){
  var D=JSON.parse(document.getElementById('news-data').textContent);
  var CH=__CH__, DUR=__DUR__;
  var COL={politics:'var(--pol)',football:'var(--foot)',art:'var(--art)',film:'var(--film)'};
  var keyOf={};D.sections.forEach(function(s){(s.items||[]).forEach(function(it,i){keyOf[s.id+'|'+it.title]=s.id+'-'+i;});});
  CH.forEach(function(c){c.key=keyOf[c.sec+'|'+c.title]||null;});
  var au=new Audio();au.preload='metadata';au.src='audio.mp3?'+D.date;
  var player=document.getElementById('player'),pp=document.getElementById('pl-pp'),bar=document.getElementById('pl-bar');
  var I_PLAY='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>';
  var I_PAUSE='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 2.5h3v11h-3zM9.5 2.5h3v11h-3z" fill="currentColor"/></svg>';
  var RATES=[1.0,1.2,1.4,0.9],rateI=0,PK='brief-pos-'+D.date;
  try{var r=+localStorage.getItem('brief-arate');if(RATES.indexOf(r)>=0)rateI=RATES.indexOf(r);}catch(e){}
  var meta=document.getElementById('lsn-meta');
  function idx(t){var i=0;for(var k=0;k<CH.length;k++)if(CH[k].start<=t+0.05)i=k;return i;}
  var ti=-1,lastEl=null;
  function show(scroll){var n=idx(au.currentTime);
    bar.style.width=((au.currentTime/(au.duration||DUR))*100)+'%';
    if(n===ti&&!scroll)return;ti=n;var c=CH[n];
    document.getElementById('pl-sec').textContent=c.secName||'Morning Brief';
    player.style.setProperty('--pc',COL[c.sec]||'var(--night-sub)');
    document.getElementById('pl-ttl').textContent=c.title;
    document.getElementById('pl-pos').textContent=(n+1)+' / '+CH.length;
    if(lastEl)lastEl.classList.remove('speaking');lastEl=null;
    var e=c.key&&document.querySelector('[data-k="'+c.key+'"]');
    if(e){if(e.tagName==='DETAILS')e.open=true;e.classList.add('speaking');lastEl=e;
      if(scroll&&!document.hidden){window.scrollTo({top:e.getBoundingClientRect().top+window.scrollY-60,behavior:'smooth'});}}
    if('mediaSession' in navigator){try{navigator.mediaSession.metadata=new MediaMetadata({title:c.title,artist:(c.secName||'朝のニュース'),album:'朝のニュース '+D.date,
      artwork:[{src:'art/'+(c.sec||'brief')+'.png',sizes:'512x512',type:'image/png'}]});}catch(e){}}
  }
  function setPP(){var p=!au.paused;pp.innerHTML=p?I_PAUSE:I_PLAY;pp.setAttribute('aria-label',p?'一時停止':'再生');}
  function open(){player.hidden=false;document.body.classList.add('listening');}
  function play(){open();au.playbackRate=RATES[rateI];var p=au.play();if(p&&p.catch)p.catch(function(err){meta.hidden=false;meta.textContent='再生できませんでした（'+(err&&err.name||'')+'）。もう一度押してください';});}
  function go(n){n=Math.max(0,Math.min(CH.length-1,n));au.currentTime=CH[n].start+0.01;show(true);}
  au.addEventListener('play',setPP);au.addEventListener('pause',setPP);
  au.addEventListener('timeupdate',function(){show(false);if(Math.floor(au.currentTime)%5===0){try{localStorage.setItem(PK,au.currentTime);}catch(e){}}});
  au.addEventListener('ended',function(){try{localStorage.removeItem(PK);}catch(e){}setPP();});
  au.addEventListener('error',function(){meta.hidden=false;meta.textContent='音声を読み込めませんでした（code '+(au.error&&au.error.code)+'）';});
  document.getElementById('lsn-go').addEventListener('click',function(){
    var s=0;try{s=+localStorage.getItem(PK)||0;}catch(e){}
    if(au.currentTime<1&&s>5)au.currentTime=Math.max(0,s-3);
    play();show(true);
  });
  pp.addEventListener('click',function(){au.paused?play():au.pause();});
  document.getElementById('pl-prev').addEventListener('click',function(){var n=idx(au.currentTime);go(au.currentTime-CH[n].start>3?n:n-1);});
  document.getElementById('pl-next').addEventListener('click',function(){go(idx(au.currentTime)+1);});
  document.getElementById('pl-stop').addEventListener('click',function(){au.pause();player.hidden=true;document.body.classList.remove('listening');if(lastEl)lastEl.classList.remove('speaking');});
  var rb=document.getElementById('pl-rate');
  function setRate(){rb.textContent=RATES[rateI].toFixed(1)+'×';au.playbackRate=RATES[rateI];}
  setRate();
  rb.addEventListener('click',function(){rateI=(rateI+1)%RATES.length;setRate();try{localStorage.setItem('brief-arate',RATES[rateI])}catch(e){}});
  if('mediaSession' in navigator){var ms=navigator.mediaSession;
    try{ms.setActionHandler('play',play);ms.setActionHandler('pause',function(){au.pause();});
      ms.setActionHandler('previoustrack',function(){document.getElementById('pl-prev').click();});
      ms.setActionHandler('nexttrack',function(){document.getElementById('pl-next').click();});
      ms.setActionHandler('seekto',function(e){au.currentTime=e.seekTime;});}catch(e){}}
})();
</script>"""
PLAYER = PLAYER.replace("__CH__", esc(json.dumps(chap, ensure_ascii=False))).replace("__DUR__", str(V.get("duration", 0)))
h = h.replace("<!--PLAYER-->", PLAYER, 1) if m is None else h[:m.start()] + PLAYER + h[m.end():]

open(out, "w", encoding="utf-8").write(h)
print("ok", out, len(h))
