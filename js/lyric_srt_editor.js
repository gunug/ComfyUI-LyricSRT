import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "LyricSRTEditor";

function parseTime(s) {
  const m = String(s).trim().match(/^(?:(\d+):)?(\d+):(\d+)(?:[.,](\d{1,3}))?$/);
  if (m) {
    const ms = m[4] ? Number(m[4].padEnd(3, "0")) : 0;
    return Number(m[1] || 0) * 3600 + Number(m[2]) * 60 + Number(m[3]) + ms / 1000;
  }
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function fmtShort(t) {
  t = Math.max(0, t);
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
}

function fmtSrt(t) {
  let ms = Math.round(Math.max(0, t) * 1000);
  const h = Math.floor(ms / 3600000); ms -= h * 3600000;
  const m = Math.floor(ms / 60000); ms -= m * 60000;
  const s = Math.floor(ms / 1000); ms -= s * 1000;
  const p = (v, n) => String(v).padStart(n, "0");
  return `${p(h, 2)}:${p(m, 2)}:${p(s, 2)},${p(ms, 3)}`;
}

function parseSrt(text) {
  const cues = [];
  for (const block of String(text || "").replace(/\r/g, "").split(/\n\s*\n/)) {
    const lines = block.trim().split("\n");
    const ti = lines.findIndex((l) => l.includes("-->"));
    if (ti < 0) continue;
    const [a, b] = lines[ti].split("-->");
    const s = parseTime(a), e = parseTime(b);
    if (s == null || e == null) continue;
    cues.push({ s, e, t: lines.slice(ti + 1).join(" ").trim() });
  }
  return cues;
}

function toSrt(cues) {
  return cues
    .slice()
    .sort((x, y) => x.s - y.s)
    .map((c, i) => `${i + 1}\n${fmtSrt(c.s)} --> ${fmtSrt(c.e)}\n${c.t}\n`)
    .join("\n");
}

function el(tag, style, text) {
  const e = document.createElement(tag);
  if (style) e.style.cssText = style;
  if (text != null) e.textContent = text;
  return e;
}

const BTN = "background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:2px 6px;cursor:pointer;font-size:12px;";

function buildEditor(node) {
  const root = el("div", "display:flex;flex-direction:column;gap:6px;width:100%;height:100%;box-sizing:border-box;padding:4px;font-family:sans-serif;color:#ddd;");
  const audio = el("audio", "width:100%;");
  audio.controls = true;
  const now = el("div", "min-height:44px;background:#111;border-radius:6px;padding:8px;font-size:20px;text-align:center;color:#ffd75e;", "▶ 실행하면 자막과 오디오가 여기 나타남");
  const bar = el("div", "display:flex;gap:4px;flex-wrap:wrap;align-items:center;");
  const list = el("div", "flex:1;overflow:auto;background:#1b1b1b;border-radius:6px;min-height:200px;");
  const status = el("div", "font-size:12px;color:#9a9;");
  root.append(audio, now, bar, list, status);

  const st = { cues: [], audio: null, path: "", rows: [], active: -1 };

  const persist = () => {
    node.properties = node.properties || {};
    node.properties.lyric_srt_state = { srt: toSrt(st.cues), audio: st.audio, path: st.path };
  };

  const mkBtn = (label, title, fn) => {
    const b = el("button", BTN, label);
    b.title = title;
    b.onclick = (ev) => { ev.preventDefault(); ev.stopPropagation(); fn(); };
    return b;
  };

  function render() {
    st.cues.sort((x, y) => x.s - y.s);
    list.innerHTML = "";
    st.rows = [];
    st.cues.forEach((c, i) => {
      const row = el("div", "display:flex;gap:4px;align-items:center;padding:3px 4px;border-bottom:1px solid #2a2a2a;");
      const idx = el("span", "width:26px;color:#888;font-size:11px;text-align:right;", String(i + 1));
      const sIn = el("input", "width:68px;background:#222;color:#cfe;border:1px solid #444;font-size:12px;");
      const eIn = el("input", "width:68px;background:#222;color:#fcd;border:1px solid #444;font-size:12px;");
      const tIn = el("input", "flex:1;min-width:120px;background:#222;color:#eee;border:1px solid #444;font-size:13px;");
      sIn.value = fmtShort(c.s); eIn.value = fmtShort(c.e); tIn.value = c.t;
      sIn.onchange = () => { const v = parseTime(sIn.value); if (v != null) { c.s = v; persist(); render(); } };
      eIn.onchange = () => { const v = parseTime(eIn.value); if (v != null) { c.e = v; persist(); render(); } };
      tIn.onchange = () => { c.t = tIn.value; persist(); };
      row.append(
        idx,
        mkBtn("▶", "이 줄부터 재생", () => { audio.currentTime = Math.max(0, c.s - 0.5); audio.play(); }),
        sIn,
        mkBtn("[", "시작 = 현재 재생 위치", () => { c.s = audio.currentTime; persist(); render(); }),
        eIn,
        mkBtn("]", "끝 = 현재 재생 위치", () => { c.e = audio.currentTime; persist(); render(); }),
        tIn,
        mkBtn("✕", "줄 삭제", () => { st.cues.splice(i, 1); persist(); render(); }),
      );
      for (const inp of [sIn, eIn, tIn]) inp.addEventListener("keydown", (ev) => ev.stopPropagation());
      list.append(row);
      st.rows.push(row);
    });
    status.textContent = `${st.cues.length}줄`;
  }

  function shiftAll(d) {
    st.cues.forEach((c) => { c.s = Math.max(0, c.s + d); c.e = Math.max(0, c.e + d); });
    persist(); render();
  }

  bar.append(
    mkBtn("+ 줄 추가(현재 위치)", "현재 재생 위치에 새 줄", () => {
      const t = audio.currentTime || 0;
      st.cues.push({ s: t, e: t + 2, t: "새 가사" });
      persist(); render();
    }),
    mkBtn("전체 −0.1s", "모든 자막을 0.1초 앞으로", () => shiftAll(-0.1)),
    mkBtn("전체 +0.1s", "모든 자막을 0.1초 뒤로", () => shiftAll(0.1)),
    mkBtn("◀ 2s", "2초 뒤로", () => { audio.currentTime = Math.max(0, audio.currentTime - 2); }),
    mkBtn("0.5x/1x", "재생 속도 전환", () => { audio.playbackRate = audio.playbackRate === 1 ? 0.5 : 1; status.textContent = `속도 ${audio.playbackRate}x`; }),
  );

  const post = async (url, body) => {
    const r = await api.fetchApi(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return r.json();
  };
  const setWidget = (name, value) => {
    const w = node.widgets?.find((x) => x.name === name);
    if (w) { w.value = value; w.callback?.(value); }
  };
  async function saveTo(path) {
    if (!path) { status.textContent = "먼저 실행하거나 '다른 위치에 저장'으로 경로를 고르세요"; return; }
    try {
      const j = await post("/lyric_srt/save", { path, srt: toSrt(st.cues) });
      status.textContent = j.ok ? `저장됨: ${j.path}` : `저장 실패: ${j.error}`;
    } catch (e) { status.textContent = `저장 실패: ${e}`; }
  }

  const saveBar = el("div", "display:flex;gap:4px;align-items:center;flex-wrap:wrap;");
  const target = el("span", "font-size:11px;color:#8ab;flex:1;min-width:200px;word-break:break-all;", "저장 경로: (실행하면 자동 지정)");
  saveBar.append(
    mkBtn("💾 저장", "표시된 경로에 저장", () => saveTo(st.path)),
    mkBtn("📁 다른 위치에 저장…", "PC 저장 대화상자에서 위치/이름 선택", async () => {
      status.textContent = "저장 대화상자 열림 (작업표시줄 확인)…";
      try {
        const j = await post("/lyric_srt/pick_save", { path: st.path });
        if (!j.ok) { status.textContent = j.error ? `실패: ${j.error}` : "취소됨"; return; }
        st.path = j.path; persist(); showTarget();
        const sep = Math.max(j.path.lastIndexOf("\\"), j.path.lastIndexOf("/"));
        setWidget("save_dir", j.path.slice(0, sep));
        setWidget("filename", j.path.slice(sep + 1).replace(/\.srt$/i, ""));
        await saveTo(st.path);
      } catch (e) { status.textContent = `실패: ${e}`; }
    }),
    mkBtn("📂 폴더 열기", "저장 폴더를 탐색기로 열기", async () => {
      if (!st.path) return;
      const j = await post("/lyric_srt/reveal", { path: st.path });
      if (!j.ok) status.textContent = `폴더 없음 — 먼저 저장하세요`;
    }),
    target,
  );
  root.insertBefore(saveBar, list);
  function showTarget() { target.textContent = `저장 경로: ${st.path || "(실행하면 자동 지정)"}`; }

  audio.addEventListener("timeupdate", () => {
    const t = audio.currentTime;
    const i = st.cues.findIndex((c) => t >= c.s && t <= c.e);
    now.textContent = i >= 0 ? st.cues[i].t : "♪";
    if (i !== st.active) {
      if (st.rows[st.active]) st.rows[st.active].style.background = "";
      if (st.rows[i]) { st.rows[i].style.background = "#3a3420"; st.rows[i].scrollIntoView({ block: "nearest" }); }
      st.active = i;
    }
  });

  function load(srt, audioName, path) {
    st.cues = parseSrt(srt);
    st.audio = audioName || st.audio;
    st.path = path || st.path;
    showTarget();
    if (st.audio) audio.src = api.apiURL(`/view?filename=${encodeURIComponent(st.audio)}&type=temp&rand=${Math.random()}`);
    st.active = -1;
    persist(); render();
  }

  return { root, load };
}

app.registerExtension({
  name: "lyricsrt.editor",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE) return;
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      this._lyricEditor = buildEditor(this);
      this.addDOMWidget("lyric_srt_editor", "div", this._lyricEditor.root, { serialize: false, getMinHeight: () => 520 });
      this.setSize([Math.max(this.size[0], 760), Math.max(this.size[1], 720)]);
      return r;
    };
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const r = onConfigure?.apply(this, arguments);
      const s = this.properties?.lyric_srt_state;
      if (s && this._lyricEditor) this._lyricEditor.load(s.srt, s.audio, s.path);
      return r;
    };
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (msg) {
      const r = onExecuted?.apply(this, arguments);
      const d = msg?.lyric_srt?.[0];
      if (d && this._lyricEditor) this._lyricEditor.load(d.srt, d.audio, d.path);
      return r;
    };
  },
});
