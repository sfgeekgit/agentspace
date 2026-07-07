"""Textual TUI for watching a PI env's logs live: a sidebar tree of views, tail pane.

Launched from zookeeper (`env watch <env>` / menu "Watch logs"). Runs on the
terminal's alternate screen; on quit the caller's prompt/menu resumes intact.
All parsing/rendering lives in logwatch.py — this file is only the shell.

The sidebar is a tree: world/scenario views are top-level leaves; each agent is
a collapsible node whose own row streams its combined session and whose children
are the per-facet views (thoughts/says/messages/scratchpad). Enter expands.

Threading rule: the UI thread never runs subprocesses or bulk paints. Stream
stop/spawn (docker execs, 50-300ms each) and rendering live in a worker; the
backlog paints in small staleness-checked batches so keys stay responsive.
"""

import threading
from functools import partial

from rich.text import Text
from textual.app import App
from textual.containers import Horizontal
from textual.widgets import Footer, Header, RichLog, Tree

from . import logwatch


class WatchApp(App):
    CSS = """
    #views { width: 24; }
    #pane { border-left: solid $accent; padding: 0 1; }
    """
    BINDINGS = [
        ("q", "quit", "quit"),
        ("p", "toggle_follow", "pause/follow"),
        ("pagedown", "page(1)", "scroll"),
        ("pageup", "page(-1)", "scroll"),
    ]

    def __init__(self, host: str, container: str):
        super().__init__()
        self.host, self.container = host, container
        self.title = f"watch — {container}"
        self.watcher = None
        self._current = None
        self._debounce = None
        self._empty_shown = False
        self._lock = threading.Lock()  # guards watcher ownership vs pump races

    def compose(self):
        yield Header()
        with Horizontal():
            tree = Tree("views", id="views")
            tree.show_root = False       # top-level views sit flush left
            tree.guide_depth = 2         # tight indent so labels fit width 24
            yield tree
            pane = RichLog(id="pane", wrap=True, max_lines=5000)
            pane.can_focus = False  # tree is the only focusable → owns arrows
            yield pane
        yield Footer()

    def on_mount(self):
        # view_tree runs docker execs — worker, per the threading rule; the
        # sidebar fills in (and the first view starts) when it returns.
        self.run_worker(self._load_views, thread=True)

    def _load_views(self):
        tree = logwatch.view_tree(self.host, self.container)
        self.call_from_thread(self._show_views, tree)

    def _show_views(self, tree):
        self.views = {}
        widget = self.query_one(Tree)
        for view, kids in tree:
            self.views[view.name] = view
            if kids:                                     # agent: collapsible node
                node = widget.root.add(view.name, data=view.name)
                for kid in kids:
                    self.views[kid.name] = kid
                    # child label is just the facet (strip the "aid:" prefix)
                    node.add_leaf(kid.name.split(":", 1)[-1], data=kid.name)
            else:                                        # world/scenario: leaf
                widget.root.add_leaf(view.name, data=view.name)
        widget.focus()
        self._switch(next(iter(self.views)))

    # Highlight IS selection. Debounced so holding an arrow scans the tree
    # freely and only the node you rest on starts a stream. An agent's own row
    # streams its combined session; Enter expands it to the facet children.
    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted):
        name = event.node.data
        if not name:
            return
        if self._debounce:
            self._debounce.stop()
        self._debounce = self.set_timer(0.25, lambda: self._switch(name))

    def action_page(self, direction: int):
        pane = self.query_one(RichLog)
        (pane.scroll_page_down if direction > 0 else pane.scroll_page_up)()

    def action_toggle_follow(self):
        pane = self.query_one(RichLog)
        pane.auto_scroll = not pane.auto_scroll
        self.notify("following" if pane.auto_scroll else "paused — scroll freely",
                    timeout=2)

    def _switch(self, name: str):
        if name == self._current:
            return
        self._current = name
        self.sub_title = name
        pane = self.query_one(RichLog)
        pane.clear()
        pane.auto_scroll = True
        self._empty_shown = False
        with self._lock:
            old, self.watcher = self.watcher, None
        self.run_worker(partial(self._pump, name, old), thread=True)

    def _pump(self, name, old):
        """Worker: stop the old stream, spawn the new one, feed the pane.
        `self._current != name` at any point = the user moved on; abandon.
        Ownership handoff is locked: two overlapping pumps must not both
        claim self.watcher (the loser would leak a live stream)."""
        if old:
            old.stop()
        watcher = logwatch.Watcher(self.host, self.container, self.views[name],
                                   backfill=200)
        with self._lock:
            if self._current != name:
                stale = True
            else:
                stale, self.watcher = False, watcher
        if stale:
            watcher.stop()
            return
        pane = self.query_one(RichLog)
        first = True
        for chunk in watcher.events():
            if first and not chunk:  # empty backlog → placeholder until live lines
                self.call_from_thread(self._show_empty, name, pane)
            first = False
            for i in range(0, len(chunk), 50):
                if self._current != name:
                    return
                lines = [Text.from_markup(logwatch.render(e))
                         for e in chunk[i:i + 50]]
                # call_from_thread waits for the paint — natural throttling
                self.call_from_thread(self._write_lines, name, pane, lines)

    def _show_empty(self, name, pane):
        if self._current != name:
            return
        self._empty_shown = True
        pane.write(Text("nothing to see here yet", style="dim italic"))

    def _write_lines(self, name, pane, lines):
        if self._current != name:  # paint dispatched just before a switch
            return
        if self._empty_shown:       # real content arrived — drop the placeholder
            pane.clear()
            self._empty_shown = False
        for t in lines:
            pane.write(t)

    def on_unmount(self):
        with self._lock:
            self._current = None  # any in-flight pump now stops its watcher
            watcher, self.watcher = self.watcher, None
        if watcher:
            watcher.stop()
