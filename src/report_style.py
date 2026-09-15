"""CSS for the HTML report. Kept separate so report.py stays about data."""

CSS = """
:root{
  --ink:#1a1d23; --muted:#5d6570; --line:#e2e6ec; --bg:#fbfcfd;
  --good:#2e9e5b; --ok:#d9962a; --bad:#cc4b3d; --accent:#1f6fb4;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:0 22px 90px}
header{background:linear-gradient(160deg,#16324f,#1f6fb4);color:#fff;
  padding:46px 22px 40px;margin-bottom:34px}
header .wrap{padding-bottom:0}
h1{font-size:30px;margin:0 0 8px;letter-spacing:-.4px}
header .sub{opacity:.85;font-size:15px;margin:0}
h2{font-size:22px;margin:46px 0 14px;padding-bottom:7px;border-bottom:2px solid var(--line)}
h3{font-size:17px;margin:26px 0 8px}
p{margin:0 0 13px}
.lead{font-size:18px;line-height:1.6}
.muted{color:var(--muted);font-size:14px}
code{background:#eef1f5;padding:1px 5px;border-radius:4px;font-size:.88em}

.verdict{background:#fff;border:1px solid var(--line);border-left:5px solid var(--accent);
  border-radius:8px;padding:20px 22px;margin:20px 0}
.verdict.warn{border-left-color:var(--ok)}
.callout{background:#fff8e8;border:1px solid #f0dcae;border-radius:8px;padding:15px 18px;margin:18px 0}
.callout.bad{background:#fdf0ee;border-color:#f0c4bd}
.callout.good{background:#eef8f1;border-color:#bfe3cc}

table{border-collapse:collapse;width:100%;margin:16px 0;font-size:14.5px;background:#fff}
th,td{padding:9px 11px;text-align:left;border-bottom:1px solid var(--line)}
th{background:#f2f5f8;font-weight:600;font-size:13px;text-transform:uppercase;
  letter-spacing:.4px;color:var(--muted)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
tr.hl td{background:#fdf3f2;font-weight:600}

.tag{display:inline-block;padding:1px 8px;border-radius:11px;font-size:12px;font-weight:600}
.tag.good{background:#e2f3e8;color:#1d7a42}
.tag.ok{background:#fcf0da;color:#9c6a12}
.tag.bad{background:#fbe6e3;color:#a63528}

/* metric scorecard */
.metric{background:#fff;border:1px solid var(--line);border-radius:8px;
  padding:16px 18px;margin:14px 0}
.metric .top{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.metric .name{font-weight:650;font-size:16px}
.metric .val{font-size:23px;font-weight:700;font-variant-numeric:tabular-nums}
.metric .dir{font-size:12.5px;color:var(--muted)}
.bar{position:relative;height:26px;margin:26px 0 54px;border-radius:5px;overflow:visible;
  background:#eef1f5}
.band{position:absolute;top:0;height:26px}
.band.good{background:rgba(46,158,91,.26)}
.band.ok{background:rgba(217,150,42,.26)}
.band.bad{background:rgba(204,75,61,.22)}
.bandlab{position:absolute;top:29px;font-size:10.5px;color:var(--muted);
  transform:translateX(-50%);white-space:nowrap}
.marker{position:absolute;top:-7px;width:3px;height:40px;background:var(--ink);
  border-radius:2px;transform:translateX(-1.5px)}
.marker span{position:absolute;top:-19px;left:50%;transform:translateX(-50%);
  background:var(--ink);color:#fff;font-size:11.5px;font-weight:600;
  padding:1px 7px;border-radius:4px;white-space:nowrap}
.anchor{position:absolute;top:0;width:1px;height:26px;background:#8b95a1}
/* Reference anchors sit on a second row, so they cannot collide with the
   qualitative band labels directly above them. */
.anchor span{position:absolute;top:45px;left:50%;transform:translateX(-50%);
  font-size:10.5px;color:#98a2ae;font-style:italic;white-space:nowrap}

figure{margin:20px 0;background:#fff;border:1px solid var(--line);
  border-radius:8px;padding:14px}
figure img{width:100%;display:block;border-radius:4px}
figcaption{font-size:13.5px;color:var(--muted);margin-top:10px}

.gloss{background:#fff;border:1px solid var(--line);border-radius:8px;
  padding:15px 18px;margin:11px 0}
.gloss .term{font-weight:650;font-size:16.5px}
.gloss .full{color:var(--muted);font-weight:400;font-size:14px}
.gloss .care{font-size:14.5px;color:#3f4752;margin-top:7px;
  border-left:3px solid var(--line);padding-left:11px}
a.jump{color:var(--accent);text-decoration:none;border-bottom:1px dotted var(--accent)}
a.jump:hover{background:#eaf2fa}

/* provider guide blocks */
.provider{padding:15px 18px;margin:11px 0}
.provider .term{font-weight:650;font-size:16.5px}
.provider ul{margin:8px 0 0;padding-left:20px}
.provider li{margin:4px 0}
/* "Work with us": the section is the pipeline it sells. Four stages sit on a
   conduit with a pulse running down it; each lights as the pulse arrives,
   carrying the number this build produced at that stage. The ignition delays
   are derived from the pulse geometry, not picked by eye: the pulse is 22% of
   the track wide and translates -110% -> 370% of its own width over the first
   46% of a 7.2s cycle, so its leading edge passes the stage centres
   (12.5/37.5/62.5/87.5%) at 0.46s + i * 0.78s. It loops instead of firing on
   scroll because the section sits far down a long page. Mirrors the .rig block
   in src/web/app.css, in this file's flat palette. */
.provider{background:#fff;border:1px solid var(--line);border-radius:8px}
.rig{position:relative;overflow:hidden;margin:18px 0 0;padding:26px 26px 20px;
  border:1px solid var(--line);border-radius:10px;
  background:radial-gradient(120% 130% at 4% -15%,rgba(31,111,180,.13),
    rgba(31,111,180,0) 60%),#fff}
.rig::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.55;
  background-image:linear-gradient(#eef1f5 1px,transparent 1px),
    linear-gradient(90deg,#eef1f5 1px,transparent 1px);
  background-size:28px 28px;
  -webkit-mask-image:linear-gradient(180deg,#000,transparent 70%);
  mask-image:linear-gradient(180deg,#000,transparent 70%)}
.rig>*{position:relative}
.rig-kick,.stage .sname{font:600 11.5px/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.rig-kick{display:flex;align-items:center;gap:9px;margin:0 0 12px}
.rig-kick .led{width:8px;height:8px;border-radius:50%;background:var(--good);
  animation:rig-ping 2.8s ease-out infinite}
@keyframes rig-ping{0%{box-shadow:0 0 0 0 rgba(46,158,91,.6)}
  70%,100%{box-shadow:0 0 0 10px rgba(46,158,91,0)}}
.rig-claim{font-size:clamp(25px,3.6vw,34px);line-height:1.1;font-weight:750;
  letter-spacing:-.9px;margin:0 0 12px;color:#000}
.rig-claim em{font-style:normal;color:var(--accent)}
.rig-lede{max-width:66ch;margin:0 0 11px}
.rig-lede i{font-style:normal;font-weight:650;color:#000}
.rig-flow{position:relative;list-style:none;display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));gap:20px;margin:26px 0 0;padding:34px 0 0}
.rig-flow::before{content:"";position:absolute;top:12px;left:7px;right:7px;height:2px;
  border-radius:2px;background:var(--line)}
.rig-flow::after{content:"";position:absolute;top:11px;left:7px;width:22%;height:4px;
  border-radius:4px;background:linear-gradient(90deg,rgba(31,111,180,0),
    rgba(31,111,180,.6),var(--accent));
  filter:drop-shadow(0 0 7px rgba(31,111,180,.75));
  animation:rig-pulse 7.2s linear infinite}
@keyframes rig-pulse{0%{transform:translateX(-110%);opacity:0}6%{opacity:1}
  46%{transform:translateX(370%);opacity:1}
  56%,100%{transform:translateX(370%);opacity:0}}
.stage{position:relative;display:flex;flex-direction:column;gap:3px}
.stage .node{position:absolute;top:-28px;left:0;width:14px;height:14px;border-radius:50%;
  box-sizing:border-box;background:#fff;border:2px solid var(--line);
  animation:rig-ignite 7.2s linear infinite;animation-delay:calc(.46s + var(--i) * .78s)}
.stage .node::after{content:"";position:absolute;inset:-2px;border-radius:50%;
  animation:rig-ring 7.2s linear infinite;animation-delay:inherit}
@keyframes rig-ignite{0%,1%{background:#fff;border-color:var(--line)}
  4%,88%{background:var(--accent);border-color:var(--accent)}
  96%,100%{background:#fff;border-color:var(--line)}}
@keyframes rig-ring{0%{box-shadow:0 0 0 0 rgba(31,111,180,.55)}
  14%,100%{box-shadow:0 0 0 13px rgba(31,111,180,0)}}
.stage .sval{font-size:23px;font-weight:700;letter-spacing:-.5px;line-height:1.25;
  font-variant-numeric:tabular-nums;color:#000;
  animation:rig-wake 7.2s linear infinite;animation-delay:calc(.46s + var(--i) * .78s)}
/* Dimmed, never hidden: an animation that withholds a number until the loop
   reaches it is a loading spinner. */
@keyframes rig-wake{0%,1%{opacity:.5}4%,90%{opacity:1}97%,100%{opacity:.5}}
.stage .sunit{font-size:12.5px;line-height:1.4;color:var(--accent)}
.stage .swhy{margin-top:7px;padding-top:9px;border-top:1px solid var(--line);
  font-size:13.5px;line-height:1.5;color:var(--muted)}
.rig-catch{display:flex;align-items:flex-start;gap:10px;margin:24px 0 0;padding:12px 14px;
  font-size:13.5px;border-radius:6px;background:#fff8e8;border:1px solid #f0dcae}
.rig-catch .dot{flex:none;width:9px;height:9px;margin-top:6px;border-radius:50%;
  background:var(--ok);animation:rig-blink 2.2s steps(1,end) infinite}
@keyframes rig-blink{0%,60%{opacity:1}61%,100%{opacity:.22}}
.rig-run{display:flex;flex-wrap:wrap;align-items:center;gap:11px 16px;margin-top:20px;
  padding-top:16px;border-top:1px dashed var(--line)}
.rig-run .cmd{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:13px;color:var(--ink);background:#f2f5f8;border:1px solid var(--line);
  padding:6px 10px;border-radius:6px}
.rig-run .cmd::after{content:"\\2596";color:var(--accent);margin-left:2px;
  animation:rig-caret 1.1s steps(1,end) infinite}
@keyframes rig-caret{0%,50%{opacity:1}51%,100%{opacity:0}}
.rig-run .ok{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:12.5px;color:var(--good)}
.rig-run .btn{margin:0 0 0 auto}
.btn{display:inline-block;padding:10px 20px;border-radius:999px;
  background:var(--accent);color:#fff;text-decoration:none;font-weight:650;
  transition:transform .15s,filter .15s}
.btn:hover{filter:brightness(1.1);transform:translateY(-1px)}
.contact{margin-top:14px;font-size:14px;color:var(--muted)}
/* Below this width four stages become four stubs: stand the pipeline up. */
@media (max-width:860px){
  .rig{padding:20px 18px 16px}
  .rig-flow{grid-template-columns:minmax(0,1fr);gap:24px;padding:2px 0 2px 28px}
  .rig-flow::before{top:7px;bottom:7px;left:6px;right:auto;width:2px;height:auto}
  .rig-flow::after{top:0;left:5px;width:4px;height:18%;
    background:linear-gradient(180deg,rgba(31,111,180,0),rgba(31,111,180,.6),var(--accent));
    animation-name:rig-pulse-v}
  .stage .node{top:1px;left:-28px}}
@keyframes rig-pulse-v{0%{transform:translateY(-110%);opacity:0}6%{opacity:1}
  46%{transform:translateY(470%);opacity:1}
  56%,100%{transform:translateY(470%);opacity:0}}
/* Park the loop lit rather than freezing it mid-sweep; the travelling pulse
   has no still frame worth keeping, so it goes. */
@media (prefers-reduced-motion:reduce){
  .rig *,.rig *::before,.rig *::after{animation:none !important}
  .rig-flow::after{display:none}
  .stage .node{background:var(--accent);border-color:var(--accent)}
  .stage .sval{opacity:1}}

/* provider -> model -> app -> decision flow diagram (Task 8). Themed via
   classes only, like the calibration curve, so all three themes restyle it. */
.flow-box{fill:#fff;stroke:var(--line);stroke-width:1.5}
.flow-title{font-size:14px;font-weight:650;fill:var(--ink)}
.flow-sub{font-size:11px;fill:var(--muted)}
.flow-arrow{fill:var(--muted)}

svg .pt{cursor:pointer}
svg .pt:hover circle{stroke-width:3}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:#fff;
  padding:7px 11px;border-radius:6px;font-size:12.5px;line-height:1.5;
  opacity:0;transition:opacity .12s;z-index:10;white-space:nowrap}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);
  font-size:13px;color:var(--muted)}
"""
