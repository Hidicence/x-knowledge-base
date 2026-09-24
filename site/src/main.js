import './scrollcraft.css';
import './style.css';
import './scrollcraft.js';
import { createIcons, ArrowUpRight, Bookmark, FileText, MessagesSquare, Fingerprint, Link, ScanLine, Quote, CornerDownRight, Plus, History, Network, Split, Copy, Check } from 'lucide';

const icons = { ArrowUpRight, Bookmark, FileText, MessagesSquare, Fingerprint, Link, ScanLine, Quote, CornerDownRight, Plus, History, Network, Split, Copy, Check };
createIcons({ icons });
const engine = window.ScrollCraft.mount(document.querySelector('#page'));
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const fine = matchMedia('(hover:hover) and (pointer:fine)');
const $ = selector => document.querySelector(selector);
const hero = $('#home'), scene = $('.hero-scene'), thread = $('#thread'), stage = $('.thread-stage');
const back = $('.scene-back'), subject = $('.scene-subject'), front = $('.scene-front'), title = $('.hero-title');
const fragments = [...document.querySelectorAll('.source-fragment')];
const card = $('.assembled-card'), stamp = $('.review-stamp'), receipt = $('.recall-receipt');
const line = $('.thread-draw');
const lineLength = line.getTotalLength();
line.style.strokeDasharray = lineLength;
let px = 0, py = 0, mx = 0, my = 0;
let pending = false;
const clamp = v => Math.max(0,Math.min(1,v));
const smooth = (a,b,v) => { const t=clamp((v-a)/(b-a));return t*t*(3-2*t); };
let dimensions;
function measure() {
  dimensions = { heroTop:hero.offsetTop, heroHeight:hero.offsetHeight, threadTop:thread.offsetTop, threadHeight:thread.offsetHeight, vh:innerHeight };
  requestPaint();
}
function show(el,opacity,transform) {
  el.style.opacity = opacity.toFixed(3);
  el.style.transform = transform;
  el.style.visibility=opacity<.015?'hidden':'visible';
  el.setAttribute('aria-hidden',String(opacity<.5));
}
function paint() {
  pending=false;
  if(!dimensions) return;
  const mobile=innerWidth<=700;
  const hp=reduced.matches?0:clamp((scrollY-dimensions.heroTop)/Math.max(dimensions.heroHeight*.78,1));
  back.style.transform=`translate3d(${mx*.3}px,${-hp*45+my*.3}px,0) scale(${1+hp*.08})`;
  subject.style.transform=`translate3d(${mx}px,${-hp*(mobile?55:95)+my}px,0) rotate(${hp*-7}deg) scale(${1+hp*.12})`;
  front.style.transform=`translate3d(${mx*1.8}px,${-hp*(mobile?90:155)+my*1.8}px,0) scale(${1+hp*.2})`;
  title.style.transform=`translateY(${-hp*38}px) scale(${1-hp*.08})`;
  title.style.opacity=String(1-hp*.42);
  scene.dataset.scVerifyState=`back:${(-hp*45).toFixed(1)},subject:${(-hp*95).toFixed(1)},front:${(-hp*155).toFixed(1)}`;
  if(reduced.matches){
    [...fragments,card,stamp,receipt].forEach(el=>{el.style.cssText='';el.removeAttribute('aria-hidden');});
    stage.dataset.scVerifyHold='true';
    return;
  }
  const p=clamp((scrollY-dimensions.threadTop)/Math.max(dimensions.threadHeight-dimensions.vh,1));
  const gather=smooth(.05,.38,p), form=smooth(.26,.43,p), gate=smooth(.43,.6,p), transfer=smooth(.66,.91,p);
  fragments.forEach((el,i)=>{
    const directions=mobile?[[55,45],[-55,0],[15,-110]]:[[230,100],[-180,0],[70,-180]];
    const rotations=[-12,11,-5];
    show(el,1-smooth(.24,.43,p),`translate(${directions[i][0]*gather}px,${directions[i][1]*gather}px) rotate(${rotations[i]*(1-gather)}deg) scale(${1-gather*.15})`);
  });
  show(card,form*(1-transfer*.88),`translate(${transfer*(mobile?-25:-115)}px,${transfer*-30}px) rotate(${transfer*-9}deg) scale(${.8+form*.2-transfer*.1})`);
  show(stamp,gate*(1-smooth(.7,.87,p)),`translateY(${(1-gate)*24}px) rotate(-8deg)`);
  show(receipt,transfer,`translate(${(1-transfer)*35}px,${(1-transfer)*80}px) rotate(${(1-transfer)*7}deg)`);
  line.style.strokeDashoffset=String(lineLength*(1-(.08+.92*p)));
  const phase=p<.38?0:p<.7?1:2;
  const words=[['收藏，開始有了形狀。','多種來源，整理成同一套知識卡。'],['知識，需要經過判斷。','候選經過審核，再沉澱為可閱讀的 Wiki。'],['下一次思考，有了來處。','不同 Agent 讀取同一層知識，也帶回來源。']][phase];
  $('.thread-status').textContent=words[0];$('.thread-description').textContent=words[1];
  stage.dataset.scVerifyState=`gather:${gather.toFixed(2)},card:${form.toFixed(2)},gate:${gate.toFixed(2)},recall:${transfer.toFixed(2)},line:${(lineLength*(1-p)).toFixed(0)}`;
}
function requestPaint(){if(!pending){pending=true;requestAnimationFrame(paint);}}
addEventListener('scroll',requestPaint,{passive:true});
addEventListener('resize',()=>{engine.layout();measure();},{passive:true});
addEventListener('pointermove',event=>{
  if(!fine.matches||reduced.matches||scrollY>hero.offsetHeight)return;
  px=(event.clientX/innerWidth-.5)*16;py=(event.clientY/innerHeight-.5)*12;
},{passive:true});
let lastFrame=0;
function pointerFrame(time){
  requestAnimationFrame(pointerFrame);
  if(document.hidden||reduced.matches||scrollY>hero.offsetHeight||time-lastFrame<32)return;
  lastFrame=time;
  if(Math.abs(px-mx)+Math.abs(py-my)<.05)return;
  mx+=(px-mx)*.12;my+=(py-my)*.12;requestPaint();
}
requestAnimationFrame(pointerFrame);
document.fonts.ready.then(()=>{engine.layout();measure();});
reduced.addEventListener('change',()=>location.reload());
measure();

// Keep a complete static composition if any of the independent hero layers fails.
Promise.all([...document.querySelectorAll('.hero-scene img')].map(img=>img.decode())).then(()=>{
  document.body.classList.add('assets-ready');measure();
}).catch(()=>{document.body.classList.add('asset-unavailable');});

const dockLinks=[...document.querySelectorAll('.contents-dock a')];
const observer=new IntersectionObserver(entries=>{
  for(const entry of entries) if(entry.isIntersecting) dockLinks.forEach(link=>{
    if(link.hash===`#${entry.target.id}`)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');
  });
},{rootMargin:'-15% 0px -55% 0px'});
document.querySelectorAll('main>section[id]').forEach(el=>observer.observe(el));
document.querySelectorAll('details').forEach(el=>el.addEventListener('toggle',()=>{engine.layout();measure();}));

const setupModes={
  local:{code:'git clone https://github.com/Hidicence/x-knowledge-base.git\ncd x-knowledge-base\npython3 scripts/xkb_init.py',label:'TERMINAL / 初次設定',note:'接著配置模型，再匯入第一份筆記。',url:'https://github.com/Hidicence/x-knowledge-base#quick-start'},
  service:{code:'python3 scripts/xkb_knowledge_service.py\n\n# Local endpoint\n# http://127.0.0.1:18972',label:'TERMINAL / 已完成初次設定',note:'依照文件設定存取權限，再接入你的 Agent。',url:'https://github.com/Hidicence/x-knowledge-base#share-it-across-agents'}
};
const tabs=[...document.querySelectorAll('[role="tab"]')];
function setMode(tab){
  const mode=setupModes[tab.dataset.mode];
  tabs.forEach(el=>{el.setAttribute('aria-selected',String(el===tab));el.tabIndex=el===tab?0:-1;});
  $('#code-panel').setAttribute('aria-labelledby',tab.id);
  $('#setup-command').textContent=mode.code;$('#code-label').textContent=mode.label;
  $('#setup-note').textContent=mode.note;$('#setup-docs').href=mode.url;
  $('#copy-status').textContent='';
  engine.layout();measure();
}
tabs.forEach(tab=>{
  tab.addEventListener('click',()=>setMode(tab));
  tab.addEventListener('keydown',e=>{
    if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
      e.preventDefault();const next=e.key==='Home'?tabs[0]:e.key==='End'?tabs.at(-1):tabs[(tabs.indexOf(tab)+1)%tabs.length];
      setMode(next);next.focus();
    }
  });
});
$('#copy-command').addEventListener('click',async()=>{
  try{
    await navigator.clipboard.writeText($('#setup-command').textContent);
    $('#copy-status').textContent='指令已複製';$('#copy-command').setAttribute('aria-label','指令已複製');
    $('#copy-command').innerHTML='<i data-lucide="check"></i>';createIcons({icons});
    setTimeout(()=>{$('#copy-command').innerHTML='<i data-lucide="copy"></i>';$('#copy-command').setAttribute('aria-label','複製指令');createIcons({icons});},1800);
  }catch{
    $('#copy-status').textContent='瀏覽器未允許剪貼簿存取，已選取指令。';
    const selection=getSelection(),range=document.createRange();range.selectNodeContents($('#setup-command'));selection.removeAllRanges();selection.addRange(range);$('#code-panel').focus();
  }
});
