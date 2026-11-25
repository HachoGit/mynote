"""理論マイノートの動作テスト（Playwright）

使い方:
  pip install playwright && python -m playwright install chromium
  python tests/run_tests.py
  （手元の Chrome を使う場合: CHROME_PATH=/path/to/chrome python tests/run_tests.py）

- リポジトリ直下を http://localhost:8765/ で配信して index.html を開く
- GitHub API は模擬サーバー（ブラウザ内でルーティング）。本物の GitHub には一切アクセスしない
- 貼り付けテストのデータは、スタディングのマイノートと同じ HTML 構造で、文章は架空のもの
  （予備校の教材の文章は、このリポジトリに絶対に入れないこと）
"""
import functools, http.server, json, os, re, sys, threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
URL = f"http://localhost:{PORT}/"

# スタディングのマイノートをコピーしたときの HTML 構造を再現した架空データ
STUDYING_LIKE = """<html><body><!--StartFragment--><div id="doc-menu" class="popUp"><ul class="doc-menu-list">
<li><div class="btn_scalable"><span class="percent">100%</span></div></li>
<li class="tool"><label for="span-memory-toggle">暗記ツール</label><div class="onoffswitch">&nbsp;</div></li></ul></div>
<div class="doc_content"><div class="doc_body"><div id="mokuji"><div class="mokuji"><div class="title">目次 Contents</div>
<div id="toc"><ol><li>・<a href="#a">１．テスト用の見出し</a></li></ol></div></div></div>
<div id="doc" class="redactor-editor"><div><h2 id="a">１．テスト用の見出し</h2></div>
<p><span class="span-bold">(１)</span><span class="span-bold">　<span class="span-memory">架空の項目名</span></span></p>
<p>　<span class="span-memory"><span class="touch">サンプル語句</span>を<span class="touch">含む</span>架空の文章である。</span></p>
<p>①　<span class="touch span-memory">一語だけの赤</span></p>
</div>
<p><span class="span-memory">本文の枠の外に出た段落</span></p>
</div></div><!--EndFragment--></body></html>"""

LEGACY = json.dumps({"notes": [{"id": "legacy1", "title": "以前から入っていたノート", "html": "<p>旧データ</p>", "updated": 1000, "folder": ""}],
                     "folders": [], "cur": "legacy1", "zoom": 90, "open": {}})

results = []
def check(name, ok, info=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info and not ok else ""))


def serve():
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("localhost", PORT), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class MockGitHub:
    def __init__(self):
        self.gists = {"old1": {"files": {"riron-mynote.json": {"content": json.dumps(
            {"app": "riron-mynote", "notes": [{"id": "x", "title": "平文の古いデータ", "html": "<p>古い</p>", "updated": 5}]})}}}}
        self.deleted = []

    def __call__(self, route, request):
        H = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}
        m = request.method
        if m == "OPTIONS":
            return route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*",
                                                      "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE"})
        if request.headers.get("authorization", "") != "Bearer ghp_TEST":
            return route.fulfill(status=401, body="{}", headers=H)
        path = request.url.split("api.github.com")[1].split("?")[0]
        if path == "/user":
            return route.fulfill(status=200, body='{"login":"me"}', headers=H)
        if path == "/gists" and m == "GET":
            return route.fulfill(status=200, headers=H, body=json.dumps(
                [{"id": k, "files": {n: {} for n in v["files"]}} for k, v in self.gists.items()]))
        if path == "/gists" and m == "POST":
            d = json.loads(request.post_data)
            self.gists["g1"] = {"files": {k: {"content": v["content"]} for k, v in d["files"].items()}, "public": d["public"]}
            return route.fulfill(status=201, body='{"id":"g1"}', headers=H)
        mm = re.match(r"/gists/(\w+)$", path)
        gid = mm.group(1) if mm else None
        if not gid or gid not in self.gists:
            return route.fulfill(status=404, body="{}", headers=H)
        if m == "GET":
            return route.fulfill(status=200, headers=H, body=json.dumps({"id": gid, "files": {
                k: {"content": v["content"], "truncated": False} for k, v in self.gists[gid]["files"].items()}}))
        if m == "PATCH":
            for k, v in json.loads(request.post_data)["files"].items():
                self.gists[gid]["files"][k] = {"content": v["content"]}
            return route.fulfill(status=200, body="{}", headers=H)
        if m == "DELETE":
            self.deleted.append(gid); self.gists.pop(gid)
            return route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*"})


def idb_titles(pg):
    return sorted(pg.evaluate("""async()=>{const d=await new Promise(r=>{const q=indexedDB.open('rironMyNote',1);q.onsuccess=()=>r(q.result)});
      return await new Promise(r=>{const g=d.transaction('kv').objectStore('kv').get('state');g.onsuccess=()=>r(g.result.notes.filter(n=>!n.trash).map(n=>n.title))})}"""))


def paste(pg, html):
    pg.evaluate("""raw=>{const ed=document.querySelector('#ed');ed.focus();const dt=new DataTransfer();dt.setData('text/html',raw);
      ed.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}))}""", html)
    pg.wait_for_timeout(300)


def select_chars(pg, a, b):
    pg.evaluate("""([a,b])=>{const ed=document.querySelector('#ed');const tw=document.createTreeWalker(ed,4);let pos=0,n,r=document.createRange(),sa=0,sb=0;
      while(n=tw.nextNode()){const L=n.length;if(!sa&&a<=pos+L){r.setStart(n,a-pos);sa=1}if(!sb&&b<=pos+L){r.setEnd(n,b-pos);sb=1}pos+=L}
      getSelection().removeAllRanges();getSelection().addRange(r)}""", [a, b])


def main():
    srv = serve()
    errors = []
    with sync_playwright() as p:
        kw = {"executable_path": os.environ["CHROME_PATH"]} if os.environ.get("CHROME_PATH") else {}
        b = p.chromium.launch(args=["--no-sandbox"], **kw)
        gh = MockGitHub()

        def dev(legacy=None, touch=False):
            ctx = b.new_context(viewport={"width": 400, "height": 800}, service_workers="block", has_touch=touch, is_mobile=touch)
            ctx.route("https://api.github.com/**", gh)
            if legacy:
                ctx.add_init_script("if(!sessionStorage.getItem('seeded')){localStorage.setItem('rironMyNote.v1',%s);sessionStorage.setItem('seeded','1')}" % json.dumps(legacy))
            pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(URL); pg.wait_for_timeout(600)
            return ctx, pg

        # ---- 赤マーカーの付け外し ----
        _, pg = dev()
        pg.click("#bNew"); pg.click("#ed"); pg.keyboard.type("あいうえおかきくけこ")
        show = lambda: pg.evaluate("()=>document.querySelector('#ed').innerHTML.replace(/<\\/?p>/g,'').replace(/<span class=\"r\">/g,'[').replace(/<\\/span>/g,']')")
        check("書式ボタンは選択がないと押せない", pg.evaluate("()=>document.querySelector('[data-c=red]').disabled"))
        select_chars(pg, 2, 6); pg.click("[data-c=red]"); check("選択範囲を赤に", show() == "あい[うえおか]きくけこ", show())
        check("赤を押しても選択は残る", pg.evaluate("()=>getSelection().toString()") == "うえおか")
        select_chars(pg, 3, 5); pg.click("[data-c=red]"); check("全部赤なら選んだ所だけ解除", show() == "あい[う]えお[か]きくけこ", show())
        select_chars(pg, 1, 4); pg.click("[data-c=red]"); check("赤とそれ以外が混ざれば全部赤", show() == "あ[いうえ]お[か]きくけこ", show())
        select_chars(pg, 0, 3); pg.keyboard.press("Control+b"); check("Ctrl+B で赤", show().startswith("[あいうえ]"), show())
        pg.keyboard.press("Control+z"); check("Ctrl+Z で戻す", show().startswith("あ[いうえ]"), show())

        # ---- スタディング形式の貼り付け ----
        pg.click("#bNew"); pg.click("#ed"); paste(pg, STUDYING_LIKE)
        t = pg.inner_text("#ed")
        check("メニューの 100%・暗記ツール を取り込まない", "100%" not in t and "暗記ツール" not in t, t[:60])
        check("目次は二重にならない", "目次" not in t)
        check("赤の数", pg.evaluate("()=>document.querySelectorAll('#ed .r').length") == 4, str(pg.evaluate("()=>document.querySelectorAll('#ed .r').length")))
        check("太字を保持", pg.evaluate("()=>document.querySelectorAll('#ed b').length") >= 1)
        check("本文枠の外の段落も取り込む", "本文の枠の外に出た段落" in t)
        check("全角スペースを保持", "　架空の項目名" in t or "　サンプル語句" in t, t[:80])

        # ---- 暗記ツール ----
        pg.click(".sw"); pg.wait_for_timeout(300)
        hidden = pg.evaluate("()=>[...document.querySelectorAll('#ed .r')].every(r=>getComputedStyle(r).color==='rgba(0, 0, 0, 0)')")
        check("暗記ツールONで赤が隠れる", hidden)
        pg.click("#ed .t >> nth=0"); pg.wait_for_timeout(200)
        check("語句をタップするとまとまり全体が開く", pg.evaluate("()=>document.querySelector('#ed .t').closest('.r').classList.contains('open')"))
        pg.click("#bNext"); pg.wait_for_timeout(200); n1 = pg.evaluate("()=>document.querySelectorAll('#ed .r.open').length")
        pg.click("#bPrev"); pg.wait_for_timeout(200); n2 = pg.evaluate("()=>document.querySelectorAll('#ed .r.open').length")
        check("次を開く／戻す", n1 == 2 and n2 == 1, f"{n1},{n2}")
        # 次を開く・戻すの規則（赤4つ：0=隠れている,1=開いている）
        def set_open(bits):
            pg.evaluate("b=>[...document.querySelectorAll('#ed .r')].forEach((r,i)=>r.classList.toggle('open',b[i]==='1'))", bits)
        state = lambda: pg.evaluate("()=>[...document.querySelectorAll('#ed .r')].map(r=>r.classList.contains('open')?1:0).join('')")
        for bits, btn, want, name in [
            ("0000", "#bNext", "1000", "全部隠れていれば一番上から開く"),
            ("0101", "#bNext", "0111", "開いている中で一番上のものに続けて開く"),
            ("0110", "#bNext", "0111", "開いている中で一番上のものに続けて開く（間が開いている）"),
            ("0011", "#bNext", "1011", "その下が全部開いていれば上に残ったものを開く"),
            ("1111", "#bPrev", "1110", "全部開いていれば一番下から戻す"),
            ("0101", "#bPrev", "0100", "開いている中で一番下から戻す"),
        ]:
            set_open(bits); pg.click(btn); pg.wait_for_timeout(150); check(f"{name}（{bits}→{want}）", state() == want, state())

        # ---- スワイプで一覧 ----
        ctx, pg = dev(touch=True)
        cdp = ctx.new_cdp_session(pg)
        def swipe(x0, y0, x1, y1):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
            for i in range(1, 9):
                cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x0 + (x1 - x0) * i / 8, "y": y0 + (y1 - y0) * i / 8}]}); pg.wait_for_timeout(25)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}); pg.wait_for_timeout(350)
        on = lambda: pg.evaluate("()=>document.querySelector('#drawer').classList.contains('on')")
        swipe(100, 450, 320, 455); check("右スワイプで一覧が開く", on())
        swipe(300, 400, 60, 405); check("左スワイプで一覧が閉じる", not on())
        swipe(10, 450, 300, 455); check("画面の端からのスワイプは無視", not on())
        swipe(200, 600, 220, 300); check("縦スクロールでは開かない", not on())
        swipe(100, 450, 320, 455); pg.go_back(); pg.wait_for_timeout(300); check("戻るで一覧だけ閉じる", not on() and pg.url == URL)

        # ---- 保存先の移行と暗号化同期（模擬GitHub、2台） ----
        pcx, pc = dev(LEGACY)
        check("旧保存場所から IndexedDB へ移行", idb_titles(pc) == ["以前から入っていたノート"], str(idb_titles(pc)))
        check("移行後に旧データを消す", pc.evaluate("()=>localStorage.getItem('rironMyNote.v1')===null"))
        def connect(pg, pw):
            pg.click("#bBak"); pg.fill("#tokIn", "ghp_TEST"); pg.fill("#passIn", pw); pg.fill("#passIn2", pw); pg.click("#bConn"); pg.wait_for_timeout(2500)
            return pg.inner_text("#toast")
        def sync(pg):
            if not pg.is_visible("#modal"): pg.click("#bBak")
            pg.click("#bSyncNow"); pg.wait_for_timeout(1500); pg.click("[data-mclose]")
        check("PC 接続", "接続しました" in connect(pc, "correct-horse-1"))
        check("暗号化前の古い同期データの削除確認", pc.is_visible("#cfDlg")); pc.click("#cfOk"); pc.wait_for_timeout(500)
        check("古い同期データを取り込んでから削除", gh.deleted == ["old1"] and "平文の古いデータ" in idb_titles(pc))
        pc.click("[data-mclose]")
        c = gh.gists["g1"]["files"]["riron-mynote.enc"]["content"]
        check("GitHub 上は暗号文だけ", c.startswith("RMNE1:") and not re.search(r"[ぁ-んァ-ン一-龥]", c))
        check("Gist は非公開", gh.gists["g1"]["public"] is False)
        pc.click("#bNew"); pc.fill("#title", "テスト理論1"); pc.click("#ed"); pc.keyboard.type("PC本文"); pc.wait_for_timeout(500); sync(pc)
        phx, ph = dev()
        check("違う合言葉は拒否", "合言葉が違います" in connect(ph, "wrong-password")); ph.click("[data-mclose]")
        check("正しい合言葉で接続", "接続しました" in connect(ph, "correct-horse-1")); ph.click("[data-mclose]")
        check("未編集のサンプルは同期に持ち込まない", "使い方" not in idb_titles(ph), str(idb_titles(ph)))
        ph.click("#bList"); ph.click("text=テスト理論1"); ph.click("#ed"); ph.keyboard.press("End"); ph.keyboard.type("＋スマホ"); ph.wait_for_timeout(500)
        sync(ph); sync(pc)
        check("スマホの編集が PC に届く", pc.inner_text("#ed") == "PC本文＋スマホ", pc.inner_text("#ed"))
        ph.reload(); ph.wait_for_timeout(2500); ph.click("#bBak")
        check("開き直しても合言葉の再入力は不要", ph.inner_text("#syncInfo").startswith("同期済み"), ph.inner_text("#syncInfo")); ph.click("[data-mclose]")

        # ---- オフライン（Service Worker 有効） ----
        ctx = b.new_context(viewport={"width": 400, "height": 800}); pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL); pg.wait_for_timeout(1500)
        ctx.set_offline(True); pg.reload(); pg.wait_for_timeout(800)
        check("オフラインで起動できる", pg.evaluate("()=>document.querySelectorAll('#ed .r').length") > 0)

        b.close()
    srv.shutdown()
    check("ページ内のエラーなし", not errors, "; ".join(errors[:3]))
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
