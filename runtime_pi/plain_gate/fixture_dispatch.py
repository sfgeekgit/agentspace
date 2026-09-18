"""Plain-mode gate dispatcher: two wakes of one agent, record what it submitted."""


def run(api, params):
    state = api.load_state(default={"got": []})
    for payload in ("You stand in a square. What do you do?", "You walk. Then what?"):
        api.wake("a1", payload)
        state["got"].append(api.collect("a1", default=None))
        api.save_state(state)
