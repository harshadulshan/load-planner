"""
The 3D loading view: a self-contained HTML page (WebGL, no outside libraries) where the boxes fly in through the door
in loading order. Drag to turn, scroll or pinch to zoom, hover a box for what it is; play, pause and scrub the
loading steps; colour by drop-off stop or by item; the balance point of what is loaded so far is shown on the floor.
"""

from __future__ import annotations

import json


# validated categorical palette (light, dark), in fixed order
PALETTE = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
           ("#e87ba4", "#d55181"), ("#008300", "#008300"), ("#6250d6", "#9085e9"), ("#e34948", "#e66767")]
OTHER = ("#9b9a95", "#7d7c78")


def item_of(name):
    return str(name).split(" → ")[0]


def color_groups(labels, weights):
    """Fixed-order colours for up to 8 groups; past 8, the 7 largest keep a colour and the rest are 'Other'."""
    order = list(dict.fromkeys(labels))
    if len(order) > 8:
        size = {g: 0.0 for g in order}
        for g, w in zip(labels, weights):
            size[g] += w
        keep = sorted(order, key=lambda g: -size[g])[:7]
        order = [g for g in order if g in keep]
        return {g: (PALETTE[i] if g in order else OTHER) for i, g in
                enumerate(order + [g for g in dict.fromkeys(labels) if g not in order])}, order + ["Other"]
    return {g: PALETTE[i] for i, g in enumerate(order)}, order


def plan_payload(plan, stop_names=None):
    """What the page needs from a plan (plain JSON)."""
    b = plan.boxes
    cg = plan.cargo
    t = b[:, 6]
    vol = (b[:, 3:6] - b[:, :3]).prod(axis=1)
    items = [item_of(cg.names[k]) for k in t]
    stops = [int(cg.stop[k]) for k in t]
    by_item, item_order = color_groups(items, vol)
    stop_order = sorted(set(stops))
    by_stop = {s: PALETTE[i % 8] if len(stop_order) <= 8 else (PALETTE[i] if i < 7 else OTHER)
               for i, s in enumerate(stop_order)}
    sn = stop_names or {}
    labels_stop = [f"Stop {s}" + (f" · {sn[s]}" if s in sn else "") for s in stop_order]
    lab = ["upright" if (b[i, 5] - b[i, 2]) == cg.dims[t[i]][2] else "on its side" for i in range(len(b))]
    v = plan.vehicle
    return {
        "L": int(v.L), "W": int(v.W), "H": int(v.H), "vehicle": v.name,
        "boxes": b[:, :6].astype(int).tolist(),
        "w": [float(x) for x in cg.weight[t]],
        "item": items, "stop": stops, "how": lab,
        "colItem": [list(by_item[g]) for g in items],
        "colStop": [list(by_stop[s]) for s in stops],
        "legendItem": [[g, list(by_item.get(g, OTHER))] for g in item_order],
        "legendStop": [[lbl, list(by_stop[s])] for lbl, s in zip(labels_stop, stop_order)],
        "multiStop": len(stop_order) > 1,
    }


def html(plan, height=560, dark=False, stop_names=None, autoplay=True, speed=6.0, start_at=None):
    data = plan_payload(plan, stop_names)
    data.update({"dark": bool(dark), "autoplay": bool(autoplay), "speed": float(speed),
                 "startAt": len(plan.boxes) if start_at is None and not autoplay else (start_at or 0)})
    js = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")      # names cannot end the script
    return PAGE.replace("__H__", str(int(height))).replace("__DATA__", js)


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#fcfcfb;--panel:#f3f2ef;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#dcdad4;--accent:#2a78d6}
.dark{--bg:#1a1a19;--panel:#242422;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e88;--line:#3a3936;--accent:#3987e5}
html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);font:13px/1.35 "Source Sans Pro","Segoe UI",system-ui,sans-serif}
#wrap{position:relative;height:__H__px;display:flex;flex-direction:column;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--bg)}
#stage{position:relative;flex:1;min-height:0;cursor:grab;touch-action:none}
#stage.drag{cursor:grabbing}
canvas{display:block;width:100%;height:100%}
.tag{position:absolute;transform:translate(-50%,-50%);font-size:11px;color:var(--ink2);background:var(--bg);padding:1px 6px;border:1px solid var(--line);border-radius:9px;pointer-events:none;white-space:nowrap}
#tip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font-size:12px;display:none;max-width:260px;box-shadow:0 4px 14px rgba(0,0,0,.18)}
#tip b{display:block;margin-bottom:2px}
#legend{position:absolute;left:10px;top:10px;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:6px 8px;font-size:12px;max-width:42%;opacity:.95}
#legend div{display:flex;align-items:center;gap:6px;margin:2px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#legend i{width:11px;height:11px;border-radius:3px;flex:none}
#bal{position:absolute;right:10px;top:10px;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:6px 9px;font-size:12px;text-align:right;color:var(--ink2)}
#bar{display:flex;align-items:center;gap:8px;padding:8px 10px;border-top:1px solid var(--line);background:var(--panel);flex-wrap:wrap}
button,select{font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:4px 10px;cursor:pointer}
button:hover,select:hover{border-color:var(--accent)}
button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
#scrub{flex:1;min-width:120px;accent-color:var(--accent)}
#step{width:100%;color:var(--ink2);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#nogl{display:none;padding:30px;color:var(--ink2)}
</style></head><body><div id="wrap">
<div id="stage"><canvas id="c" aria-label="3D view of the load plan"></canvas><div id="legend"></div><div id="bal"></div><div id="tip"></div><div id="nogl">This browser cannot show 3D (WebGL is off). The loading steps table below has every box.</div></div>
<div id="bar"><button id="play" aria-label="Play or pause">⏸ Pause</button><button id="back" title="Back one box" aria-label="Back one box">◀</button><button id="fwd" title="Forward one box" aria-label="Forward one box">▶</button>
<input id="scrub" type="range" min="0" value="0" step="1" aria-label="Loading step">
<select id="speed" aria-label="Speed"><option value="2">Slow</option><option value="6" selected>Normal</option><option value="16">Fast</option><option value="60">Very fast</option></select>
<select id="color" aria-label="Colour boxes by"><option value="stop">Colour: stop</option><option value="item">Colour: item</option></select>
<button id="view" title="Reset the view">Reset view</button>
<div id="step"></div></div></div>
<script>
const D = __DATA__;
const N = D.boxes.length, L = D.L, W = D.W, H = D.H;
if (D.dark) document.documentElement.classList.add('dark');
const $ = id => document.getElementById(id);
const cv = $('c'), stage = $('stage');
const gl = cv.getContext('webgl', {antialias: true});
let P = D.startAt, playing = D.autoplay && N > 0, speed = D.speed, mode = D.multiStop ? 'stop' : 'item';
$('color').value = mode;
{const o=[...$('speed').options].map(x=>+x.value);const near=o.reduce((a,b)=>Math.abs(b-speed)<Math.abs(a-speed)?b:a);$('speed').value=String(near);speed=near;}
$('scrub').max = N;
const hex = h => [parseInt(h.slice(1,3),16)/255, parseInt(h.slice(3,5),16)/255, parseInt(h.slice(5,7),16)/255];
const pick = c => hex(D.dark ? c[1] : c[0]);

// ---------------------------------------------------------------- matrices (column-major)
function mul(a, b){const o=new Float32Array(16);for(let i=0;i<4;i++)for(let j=0;j<4;j++){let s=0;for(let k=0;k<4;k++)s+=a[k*4+j]*b[i*4+k];o[i*4+j]=s;}return o;}
function persp(f, asp, n, fa){const t=1/Math.tan(f/2),o=new Float32Array(16);o[0]=t/asp;o[5]=t;o[10]=(fa+n)/(n-fa);o[11]=-1;o[14]=2*fa*n/(n-fa);return o;}
function sub(a,b){return [a[0]-b[0],a[1]-b[1],a[2]-b[2]];}
function cross(a,b){return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
function norm(a){const l=Math.hypot(a[0],a[1],a[2])||1;return [a[0]/l,a[1]/l,a[2]/l];}
function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
function look(eye, at, up){const f=norm(sub(at,eye)),s=norm(cross(f,up)),u=cross(s,f),o=new Float32Array(16);
  o[0]=s[0];o[4]=s[1];o[8]=s[2];o[1]=u[0];o[5]=u[1];o[9]=u[2];o[2]=-f[0];o[6]=-f[1];o[10]=-f[2];
  o[12]=-dot(s,eye);o[13]=-dot(u,eye);o[14]=dot(f,eye);o[15]=1;return o;}

// ---------------------------------------------------------------- camera
const tgt0 = [L/2, W/2, H*0.35], R0 = Math.max(L*1.45, W*3.2, H*3.2);
let yaw0 = -0.62, pitch0 = 0.50, cam = {yaw: yaw0, pitch: pitch0, r: R0, tgt: tgt0.slice()};
function eye(){const c=Math.cos(cam.pitch);return [cam.tgt[0]+cam.r*c*Math.cos(cam.yaw), cam.tgt[1]+cam.r*c*Math.sin(cam.yaw), cam.tgt[2]+cam.r*Math.sin(cam.pitch)];}
// camera sits on the door side (x > L) looking in: yaw measured from +x

// ---------------------------------------------------------------- geometry
const FACES = [ // normal, 4 corners
 [[1,0,0],[[1,0,0],[1,1,0],[1,1,1],[1,0,1]]], [[-1,0,0],[[0,0,0],[0,0,1],[0,1,1],[0,1,0]]],
 [[0,1,0],[[0,1,0],[0,1,1],[1,1,1],[1,1,0]]], [[0,-1,0],[[0,0,0],[1,0,0],[1,0,1],[0,0,1]]],
 [[0,0,1],[[0,0,1],[1,0,1],[1,1,1],[0,1,1]]], [[0,0,-1],[[0,0,0],[0,1,0],[1,1,0],[1,0,0]]]];
const EDGES = [[0,0,0,1,0,0],[0,1,0,1,1,0],[0,0,1,1,0,1],[0,1,1,1,1,1],[0,0,0,0,1,0],[1,0,0,1,1,0],[0,0,1,0,1,1],[1,0,1,1,1,1],[0,0,0,0,0,1],[1,0,0,1,0,1],[0,1,0,0,1,1],[1,1,0,1,1,1]];
let prog, progL, bufF, bufE, nF, nE, colBuf, colBufE, vehBuf, nVeh, gridBuf, nGrid;
const STRIDE = 13; // corner3 normal3 min3 size3 idx1
function buildBoxes(){
  const f = new Float32Array(N*36*STRIDE), e = new Float32Array(N*24*STRIDE);
  let a = 0, b = 0;
  for (let i=0;i<N;i++){
    const bx = D.boxes[i], mn = [bx[0],bx[1],bx[2]], sz = [bx[3]-bx[0], bx[4]-bx[1], bx[5]-bx[2]];
    for (const [n, cs] of FACES){ for (const k of [0,1,2,0,2,3]){ f.set([...cs[k], ...n, ...mn, ...sz, i], a); a += STRIDE; } }
    for (const ed of EDGES){ e.set([ed[0],ed[1],ed[2],0,0,0,...mn,...sz,i], b); b += STRIDE; e.set([ed[3],ed[4],ed[5],0,0,0,...mn,...sz,i], b); b += STRIDE; }
  }
  bufF = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, bufF); gl.bufferData(gl.ARRAY_BUFFER, f, gl.STATIC_DRAW); nF = N*36;
  bufE = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, bufE); gl.bufferData(gl.ARRAY_BUFFER, e, gl.STATIC_DRAW); nE = N*24;
  colBuf = gl.createBuffer(); colBufE = gl.createBuffer(); setColors();
}
function setColors(){
  const src = mode === 'stop' ? D.colStop : D.colItem;
  const c = new Float32Array(N*36*3), ce = new Float32Array(N*24*3);
  for (let i=0;i<N;i++){ const rgb = pick(src[i]); for (let k=0;k<36;k++) c.set(rgb, (i*36+k)*3); for (let k=0;k<24;k++) ce.set(rgb, (i*24+k)*3); }
  gl.bindBuffer(gl.ARRAY_BUFFER, colBuf); gl.bufferData(gl.ARRAY_BUFFER, c, gl.STATIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, colBufE); gl.bufferData(gl.ARRAY_BUFFER, ce, gl.STATIC_DRAW);
  const lg = mode === 'stop' ? D.legendStop : D.legendItem;
  $('legend').innerHTML = lg.map(([t, c]) => `<div><i style="background:${D.dark ? c[1] : c[0]}"></i><span>${t.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</span></div>`).join('');
}
function buildVehicle(){
  const v = [], P8 = (x,y,z) => [x*L, y*W, z*H];
  for (const ed of EDGES) v.push(...P8(ed[0],ed[1],ed[2]), ...P8(ed[3],ed[4],ed[5]));
  vehBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vehBuf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(v), gl.STATIC_DRAW); nVeh = v.length/3;
  const g = [], step = 50;
  for (let x=0;x<=L;x+=step) g.push(x,0,0,x,W,0);
  for (let y=0;y<=W;y+=step) g.push(0,y,0,L,y,0);
  gridBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, gridBuf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(g), gl.STATIC_DRAW); nGrid = g.length/3;
}
const VS = `attribute vec3 aC,aN,aM,aS,aCol;attribute float aI;uniform mat4 uPV;uniform float uP,uL,uFly,uCur,uLine;
varying vec3 vCol;varying float vSh,vHi,vLn;
void main(){vLn=uLine;float f=clamp((uP-aI)/uFly,0.0,1.0);if(f<=0.0){gl_Position=vec4(0.0,0.0,-3.0,1.0);return;}
float e=1.0-pow(1.0-f,3.0);vec3 st=vec3(uL+60.0+aS.x*0.5,aM.y,aM.z+30.0);vec3 m=mix(st,aM,e);m.z+=sin(e*3.14159)*20.0*(1.0-step(1.0,f));
gl_Position=uPV*vec4(m+aC*aS,1.0);vec3 ld=normalize(vec3(0.45,-0.55,0.75));vSh=uLine>0.5?0.62:0.58+0.42*max(dot(aN,ld),0.0)+0.1*max(dot(aN,vec3(0.0,0.0,1.0)),0.0);
vHi=abs(aI-uCur)<0.5?1.0:0.0;vCol=aCol;}`;
const FS = `precision mediump float;varying vec3 vCol;varying float vSh,vHi,vLn;
void main(){vec3 c=vCol*vSh;if(vHi>0.5)c=vLn>0.5?vec3(1.0,1.0,1.0):mix(c,vec3(1.0),0.18);gl_FragColor=vec4(c,1.0);}`;
const VSL = `attribute vec3 aP;uniform mat4 uPV;void main(){gl_Position=uPV*vec4(aP,1.0);}`;
const FSL = `precision mediump float;uniform vec4 uC;void main(){gl_FragColor=uC;}`;
function compile(vs, fs){const p=gl.createProgram();for(const [t,s] of [[gl.VERTEX_SHADER,vs],[gl.FRAGMENT_SHADER,fs]]){const sh=gl.createShader(t);gl.shaderSource(sh,s);gl.compileShader(sh);if(!gl.getShaderParameter(sh,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(sh));gl.attachShader(p,sh);}gl.linkProgram(p);if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(p));return p;}

// ---------------------------------------------------------------- draw
let PV = null, last = performance.now(), cur = -1;
function resize(){const r=stage.getBoundingClientRect(),d=Math.min(window.devicePixelRatio||1,2);cv.width=Math.max(1,r.width*d);cv.height=Math.max(1,r.height*d);}
function attrib(p, name, size, stride, off){const l=gl.getAttribLocation(p,name);if(l<0)return;gl.enableVertexAttribArray(l);gl.vertexAttribPointer(l,size,gl.FLOAT,false,stride,off);}
function lines(buf, n, rgba){gl.useProgram(progL);gl.uniformMatrix4fv(gl.getUniformLocation(progL,'uPV'),false,PV);gl.uniform4fv(gl.getUniformLocation(progL,'uC'),rgba);gl.bindBuffer(gl.ARRAY_BUFFER,buf);
  for (let k=0;k<8;k++) gl.disableVertexAttribArray(k); attrib(progL,'aP',3,12,0);gl.drawArrays(gl.LINES,0,n);}
function drawBoxes(buf, cbuf, n, isLine){gl.useProgram(prog);const u=k=>gl.getUniformLocation(prog,k);
  gl.uniformMatrix4fv(u('uPV'),false,PV);gl.uniform1f(u('uP'),P);gl.uniform1f(u('uL'),L);gl.uniform1f(u('uFly'),1.4);gl.uniform1f(u('uCur'),cur);gl.uniform1f(u('uLine'),isLine?1:0);
  for (let k=0;k<8;k++) gl.disableVertexAttribArray(k);
  gl.bindBuffer(gl.ARRAY_BUFFER,buf);const s=STRIDE*4;attrib(prog,'aC',3,s,0);attrib(prog,'aN',3,s,12);attrib(prog,'aM',3,s,24);attrib(prog,'aS',3,s,36);attrib(prog,'aI',1,s,48);
  gl.bindBuffer(gl.ARRAY_BUFFER,cbuf);attrib(prog,'aCol',3,12,0);gl.drawArrays(isLine?gl.LINES:gl.TRIANGLES,0,n);}
function project(p){const x=PV[0]*p[0]+PV[4]*p[1]+PV[8]*p[2]+PV[12],y=PV[1]*p[0]+PV[5]*p[1]+PV[9]*p[2]+PV[13],w=PV[3]*p[0]+PV[7]*p[1]+PV[11]*p[2]+PV[15];
  const r=stage.getBoundingClientRect();return w<=0?null:[(x/w*0.5+0.5)*r.width,(1-(y/w*0.5+0.5))*r.height];}
const tags = {};
function tag(id, text, p){let el=tags[id];if(!el){el=document.createElement('div');el.className='tag';stage.appendChild(el);tags[id]=el;}el.textContent=text;const q=project(p);if(!q){el.style.display='none';return;}el.style.display='block';el.style.left=q[0]+'px';el.style.top=q[1]+'px';}
function frame(now){
  const dt=Math.min(0.1,(now-last)/1000);last=now;
  if(playing){P=Math.min(N+1.4,P+dt*speed);if(P>=N+1.4){playing=false;$('play').textContent='▶ Play';}}
  resize();gl.viewport(0,0,cv.width,cv.height);
  const bg=hex(D.dark?'#1a1a19':'#fcfcfb');gl.clearColor(bg[0],bg[1],bg[2],1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);
  const asp=cv.width/cv.height, e=eye();PV=mul(persp(0.75,asp,10,R0*8),look(e,cam.tgt,[0,0,1]));
  lines(gridBuf,nGrid,D.dark?[0.26,0.26,0.25,1]:[0.88,0.87,0.84,1]);
  gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(1,1);drawBoxes(bufF,colBuf,nF,false);gl.disable(gl.POLYGON_OFFSET_FILL);
  drawBoxes(bufE,colBufE,nE,true);
  lines(vehBuf,nVeh,D.dark?[0.75,0.74,0.70,1]:[0.32,0.32,0.30,1]);
  const done=Math.max(0,Math.min(N,Math.floor(P+1e-6)));cur=P<N+1?Math.min(N-1,Math.floor(P)):-1;
  $('scrub').value=String(Math.min(N,done));
  tag('door','Door',[L,W/2,H+18]);tag('front','Front wall',[0,W/2,H+18]);
  balance(done);stepText(done);
  requestAnimationFrame(frame);
}
let balMark = null;
function balance(k){
  let w=0,x=0,y=0,z=0;for(let i=0;i<k;i++){const b=D.boxes[i],m=D.w[i]>0?D.w[i]:(b[3]-b[0])*(b[4]-b[1])*(b[5]-b[2])*1e-6;w+=m;x+=m*(b[0]+b[3])/2;y+=m*(b[1]+b[4])/2;z+=m*(b[2]+b[5])/2;}
  const el=$('bal');if(!k||w<=0){el.style.display='none';if(tags.cog)tags.cog.style.display='none';return;}
  x/=w;y/=w;z/=w;const hasW=D.w.some(v=>v>0);
  el.style.display='block';el.innerHTML=`<b style="color:var(--ink)">Balance point of what is loaded</b><br>${(Math.abs(x-L/2)/100).toFixed(2)} m from the middle towards the ${x<L/2?'front wall':'door'}<br>${Math.abs((y-W/2)).toFixed(0)} cm ${y<W/2?'left':'right'} of the centre line`+(hasW?'':'<br><span style="color:var(--muted)">(by volume: no weights)</span>');
  tag('cog','◆ balance',[x,y,0]);
}
function stepText(k){
  const i=Math.min(N-1,Math.max(0,Math.ceil(P)-1));
  if(!N){$('step').textContent='Nothing to load.';return;}
  const b=D.boxes[i];const s=`Box ${i+1} of ${N}: ${D.item[i]}`+(D.multiStop?` · stop ${D.stop[i]}`:'')+` · ${(b[0]/100).toFixed(2)} m from the front wall, ${b[1]} cm from the left, ${b[2]?b[2]+' cm up':'on the floor'} · ${D.how[i]}`;
  $('step').textContent=k===0&&P<0.01?`${N} boxes to load. Press play or drag the slider.`:s;
}
// ---------------------------------------------------------------- hover
function ray(mx,my){const r=stage.getBoundingClientRect(),e=eye(),f=norm(sub(cam.tgt,e)),s=norm(cross(f,[0,0,1])),u=cross(s,f),t=Math.tan(0.375),asp=r.width/r.height;
  const px=(mx/r.width*2-1)*t*asp,py=(1-my/r.height*2)*t;return [e,norm([f[0]+px*s[0]+py*u[0],f[1]+px*s[1]+py*u[1],f[2]+px*s[2]+py*u[2]])];}
function hit(o,d){let best=-1,bt=1e18;const k=Math.min(N,Math.floor(P));for(let i=0;i<k;i++){const b=D.boxes[i];let t0=-1e18,t1=1e18,ok=true;
  for(let a=0;a<3;a++){if(Math.abs(d[a])<1e-12){if(o[a]<b[a]||o[a]>b[a+3]){ok=false;break;}continue;}let ta=(b[a]-o[a])/d[a],tb=(b[a+3]-o[a])/d[a];if(ta>tb)[ta,tb]=[tb,ta];t0=Math.max(t0,ta);t1=Math.min(t1,tb);if(t0>t1){ok=false;break;}}
  if(ok&&t0>0&&t0<bt){bt=t0;best=i;}}return best;}
stage.addEventListener('mousemove',ev=>{if(drag)return;const r=stage.getBoundingClientRect(),mx=ev.clientX-r.left,my=ev.clientY-r.top,[o,d]=ray(mx,my),i=hit(o,d),tip=$('tip');
  if(i<0){tip.style.display='none';return;}const b=D.boxes[i];
  tip.innerHTML=`<b>${D.item[i].replace(/&/g,'&amp;').replace(/</g,'&lt;')}</b>Box ${i+1} of ${N}`+(D.multiStop?` · stop ${D.stop[i]}`:'')+`<br>${b[3]-b[0]} × ${b[4]-b[1]} × ${b[5]-b[2]} cm, ${D.how[i]}`+(D.w[i]>0?` · ${D.w[i]} kg`:'')+`<br>${(b[0]/100).toFixed(2)} m from the front wall`;
  tip.style.display='block';tip.style.left=Math.min(mx+14,r.width-270)+'px';tip.style.top=Math.min(my+14,r.height-80)+'px';});
stage.addEventListener('mouseleave',()=>{$('tip').style.display='none';});
// ---------------------------------------------------------------- orbit
let drag=null,pinch=null;
stage.addEventListener('pointerdown',ev=>{drag={x:ev.clientX,y:ev.clientY,yaw:cam.yaw,pitch:cam.pitch};stage.classList.add('drag');stage.setPointerCapture(ev.pointerId);$('tip').style.display='none';});
stage.addEventListener('pointermove',ev=>{if(!drag||pinch)return;cam.yaw=drag.yaw-(ev.clientX-drag.x)*0.008;cam.pitch=Math.max(0.05,Math.min(1.45,drag.pitch+(ev.clientY-drag.y)*0.008));});
const endDrag=()=>{drag=null;stage.classList.remove('drag');};stage.addEventListener('pointerup',endDrag);stage.addEventListener('pointercancel',endDrag);
stage.addEventListener('wheel',ev=>{ev.preventDefault();cam.r=Math.max(R0*0.25,Math.min(R0*3,cam.r*Math.exp(ev.deltaY*0.0012)));},{passive:false});
stage.addEventListener('touchstart',ev=>{if(ev.touches.length===2){const[a,b]=ev.touches;pinch={d:Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY),r:cam.r};}},{passive:true});
stage.addEventListener('touchmove',ev=>{if(pinch&&ev.touches.length===2){const[a,b]=ev.touches;cam.r=Math.max(R0*0.25,Math.min(R0*3,pinch.r*pinch.d/Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY)));}},{passive:true});
stage.addEventListener('touchend',()=>{pinch=null;},{passive:true});
stage.addEventListener('dblclick',()=>{cam={yaw:yaw0,pitch:pitch0,r:R0,tgt:tgt0.slice()};});
// ---------------------------------------------------------------- controls
$('play').onclick=()=>{if(!playing&&P>=N+1.39)P=0;playing=!playing;$('play').textContent=playing?'⏸ Pause':'▶ Play';};
$('back').onclick=()=>{playing=false;$('play').textContent='▶ Play';P=Math.max(0,Math.floor(P-1e-6)-1)+1.4;if(P<1.4)P=0;};
$('fwd').onclick=()=>{playing=false;$('play').textContent='▶ Play';P=Math.min(N+1.4,Math.floor(P)+1+0.999);};
$('scrub').oninput=ev=>{playing=false;$('play').textContent='▶ Play';const k=+ev.target.value;P=k===0?0:k+0.4;};
$('speed').onchange=ev=>{speed=+ev.target.value;};
$('color').onchange=ev=>{mode=ev.target.value;setColors();};
$('view').onclick=()=>{cam={yaw:yaw0,pitch:pitch0,r:R0,tgt:tgt0.slice()};};
document.addEventListener('keydown',ev=>{if(ev.key===' '){ev.preventDefault();$('play').click();}else if(ev.key==='ArrowRight')$('fwd').click();else if(ev.key==='ArrowLeft')$('back').click();});
if(!playing){$('play').textContent='▶ Play';if(D.startAt>=N)P=N+1.4;}
if(!gl){$('nogl').style.display='block';cv.style.display='none';$('legend').style.display='none';}
else{try{prog=compile(VS,FS);progL=compile(VSL,FSL);buildBoxes();buildVehicle();requestAnimationFrame(frame);}catch(err){$('nogl').style.display='block';$('nogl').textContent='3D view failed: '+err.message;}}
</script></body></html>"""
