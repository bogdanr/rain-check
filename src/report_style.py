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

svg .pt{cursor:pointer}
svg .pt:hover circle{stroke-width:3}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:#fff;
  padding:7px 11px;border-radius:6px;font-size:12.5px;line-height:1.5;
  opacity:0;transition:opacity .12s;z-index:10;white-space:nowrap}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);
  font-size:13px;color:var(--muted)}
"""
