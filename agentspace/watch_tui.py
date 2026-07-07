"""Textual TUI for watching a PI env's logs live: sidebar of views, tail pane.

Launched from zookeeper (`env watch <env>` / menu "Watch logs"). Runs on the
terminal's alternate screen; on quit the caller's prompt/menu resumes intact.
All parsing/rendering lives in logwatch.py — this file is only the shell.
"""

from functools import partial

from rich.text import Text
from textual.app import App
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Label, ListItem, ListView, RichLog

from . import logwatch


class WatchApp(App):
    CSS = """
    #views { width: 24; }
    #pane { border-left: solid $accent; padding: 0 1; }
    """
    # App-level move/scroll bindings so navigation works no matter which
    # widget has focus (the pane is can_focus=False and never takes arrows).
    BINDINGS = [
        ("q", "quit", "quit"),
        ("p", "toggle_follow", "pause/follow"),
        ("down,j", "move(1)", "next view"),
        ("up,k", "move(-1)", "prev view"),
        ("pagedown", "page(1)", "scroll"),
        ("pageup", "page(-1)", "scroll"),
    ]

    def __init__(self, host: str, container: str):
        super().__init__()
        self.host, self.container = host, container
        self.title = f"watch — {container}"
        self.watcher = None
        self._debounce = None

    def compose(self):
        yield Header()
        with Horizontal():
            yield ListView(id="views")
            pane = RichLog(id="pane", wrap=True, max_lines=5000)
            pane.can_focus = False   # arrows belong to the sidebar, always
            yield pane
        yield Footer()

    def on_mount(self):
        self.views = {v.name: v for v in logwatch.views_for(self.host, self.container)}
        lv = self.query_one(ListView)
        for name in self.views:
            lv.append(ListItem(Label(name), name=name))
        lv.index = 0
        self.call_after_refresh(lv.focus)
        self._switch(next(iter(self.views)))

    # Arrow/j/k highlight IS selection — no enter needed (enter works too).
    # Debounced: mashing arrows moves the highlight instantly; the heavy part
    # (stream restart + backfill render) fires once the highlight RESTS.
    def on_list_view_highlighted(self, event: ListView.Highlighted):
        if event.item is None:
            return
        if self._debounce:
            self._debounce.stop()
        name = event.item.name
        self._debounce = self.set_timer(0.25, lambda: self._switch(name))

    def on_list_view_selected(self, event: ListView.Selected):
        self._switch(event.item.name)

    def action_move(self, delta: int):
        lv = self.query_one(ListView)
        lv.index = ((lv.index or 0) + delta) % len(self.views)

    def action_page(self, direction: int):
        pane = self.query_one(RichLog)
        (pane.scroll_page_down if direction > 0 else pane.scroll_page_up)()

    def _switch(self, name: str):
        if name == getattr(self, "_current", None):
            return   # highlight + selected both fire; start one stream, not two
        self._current = name
        if self.watcher:
            self.watcher.stop()  # EOFs the old pump thread
        pane = self.query_one(RichLog)
        pane.clear()
        pane.auto_scroll = True
        self.sub_title = name
        self.watcher = logwatch.Watcher(self.host, self.container, self.views[name])
        self.run_worker(partial(self._pump, self.watcher), thread=True)

    # Backlog arrives as one chunk (last 200 events) = ONE paint; live lines
    # trickle after. Never one callback per line — a big view would starve the
    # event loop and keys go dead (found the hard way over ssh).
    def _pump(self, watcher):
        pane = self.query_one(RichLog)
        for chunk in watcher.events(backfill=200):
            if watcher is not self.watcher:  # view switched under us
                break
            lines = [Text.from_markup(logwatch.render(e)) for e in chunk]
            self.call_from_thread(self._write_lines, pane, lines)

    def _write_lines(self, pane, lines):
        for t in lines:
            pane.write(t)

    def action_toggle_follow(self):
        pane = self.query_one(RichLog)
        pane.auto_scroll = not pane.auto_scroll
        self.notify("following" if pane.auto_scroll else "paused — scroll freely",
                    timeout=2)

    def on_unmount(self):
        if self.watcher:
            self.watcher.stop()
