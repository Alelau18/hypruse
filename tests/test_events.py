from hypruse import events


def test_parse_openwindow_normalizes_address_and_keeps_comma_titles():
    name, p = events.parse_event("openwindow>>5f2a1b,3,firefox,Meeting notes, draft 2")
    assert name == "openwindow"
    assert p["address"] == "0x5f2a1b"
    assert p["workspace"] == "3"
    assert p["class"] == "firefox"
    assert p["title"] == "Meeting notes, draft 2"


def test_parse_closewindow():
    name, p = events.parse_event("closewindow>>abc001")
    assert (name, p["address"]) == ("closewindow", "0xabc001")


def test_parse_workspace_change():
    name, p = events.parse_event("workspace>>special:scratch")
    assert (name, p["name"]) == ("workspace", "special:scratch")


def test_parse_titlev2_with_commas():
    name, p = events.parse_event("windowtitlev2>>9dead,a, very, long title")
    assert name == "windowtitlev2"
    assert p["address"] == "0x9dead"
    assert p["title"] == "a, very, long title"


def test_parse_unknown_event_passes_through():
    name, p = events.parse_event("fullscreenv2>>1,1")
    assert name == "fullscreenv2"
    assert p == {"data": "1,1"}


def test_parse_garbage_returns_none():
    assert events.parse_event("not an event line") is None
    assert events.parse_event("") is None


def test_existing_0x_prefix_untouched():
    _, p = events.parse_event("closewindow>>0xabc")
    assert p["address"] == "0xabc"


def test_parse_layer_events():
    assert events.parse_event("openlayer>>wofi") == ("openlayer", {"namespace": "wofi"})
    assert events.parse_event("closelayer>>notifications") == (
        "closelayer", {"namespace": "notifications"}
    )


def test_parse_urgent_normalizes_address():
    name, p = events.parse_event("urgent>>5f2a1b")
    assert (name, p["address"]) == ("urgent", "0x5f2a1b")


def test_parse_screencast():
    name, p = events.parse_event("screencast>>1,0")
    assert (name, p) == ("screencast", {"state": "1", "owner": "0"})


def test_a_wait_keeps_what_it_skips_for_the_next_drain():
    # V17/V42: a sequence's wait step read and dropped every other event on
    # the stream its change check drains
    import socket

    ours, theirs = socket.socketpair()
    stream = events.EventStream.__new__(events.EventStream)
    stream._sock, stream._buf, stream._kept = ours, b"", []
    theirs.sendall(b"openlayer>>wofi\nwindowtitle>>abc\nopenwindow>>beef,1,kitty,~\n")
    hit = stream.wait_for({"openwindow"}, None, 1.0, keep={"openlayer"})
    assert hit[0] == "openwindow"
    assert stream.drain(0.05) == [("openlayer", {"namespace": "wofi"})]
    theirs.sendall(b"openlayer>>wofi\nopenwindow>>beef,1,kitty,~\n")
    stream.wait_for({"openwindow"}, None, 1.0)  # a caller that keeps nothing
    assert stream.drain(0.05) == []
    ours.close()
    theirs.close()
