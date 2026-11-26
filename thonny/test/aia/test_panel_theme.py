# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""Colours of the _panel AIAView follow Thonny's themes (cortex-zamestnani#71).

Uses the syntax themes that ship with Thonny and a real Tk interpreter for colour names;
skipped where Tk cannot start.
"""

import tkinter as tk

import pytest


@pytest.fixture
def panel_view(fake_thonny):
    from thonny.plugins.aia._panel import view as module

    return module


def builtin_syntax_themes(monkeypatch):
    """name -> merged settings, the way Workbench._apply_syntax_theme merges a parent."""
    from thonny.plugins import base_syntax_themes, tomorrow_syntax_theme

    registered = {}

    class Recorder:
        def add_syntax_theme(self, name, parent, settings):
            registered[name] = (parent, settings)

        def set_default(self, name, value):
            pass

        def get_default_syntax_theme(self):
            return "Default Light"

    for module in (base_syntax_themes, tomorrow_syntax_theme):
        monkeypatch.setattr(module, "get_workbench", lambda: Recorder())
        module.load_plugin()

    def merged(name):
        parent, settings = registered[name]
        settings = settings() if callable(settings) else settings
        result = merged(parent) if parent else {}
        for key, value in settings.items():
            result.setdefault(key, {}).update(value)
        return result

    return {name: merged(name) for name in registered}


def luminance(root, color):
    r, g, b = (c / 65535 for c in root.winfo_rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# "Text" style of Thonny's UI themes: Windows (default on Windows), Clean Dark, Clean Sepia
UI_TEXTS = {
    "Windows": {"background": "SystemWindow", "foreground": "SystemWindowText"},
    "Clean Dark": {"background": "#2d2d2d", "foreground": "#9f9f9f"},
    "Clean Sepia": {"background": "#F7F6F0", "foreground": "#222222"},
}


def test_every_theme_combination_gives_readable_panel_colours(panel_view, root, monkeypatch):
    themes = builtin_syntax_themes(monkeypatch)
    assert {"Default Light", "Default Dark"} <= set(themes)

    for ui_name, ui_text in UI_TEXTS.items():
        for name, options in themes.items():
            widget, tags, border = panel_view.theme_colors(ui_text, options, root.winfo_rgb)
            bg = widget["background"]
            assert bg == ui_text["background"]
            dark = luminance(root, bg) < 0.5
            assert set(tags) == OWN_TAGS | set(panel_view.TAG_COLORS), name
            for tag, color in tags.items():
                # light text on a dark background and the other way round
                assert (luminance(root, color) > luminance(root, bg)) == dark, (
                    ui_name,
                    name,
                    tag,
                    color,
                )
            assert border is not None
            # the assistant's own lines: blue, readable, never the comment colour
            playful = tags["system_text"]
            assert tags["loading_text"] == playful
            r, g, b = root.winfo_rgb(playful)
            assert b > r and b > g, (ui_name, name, playful)
            assert panel_view.contrast(root.winfo_rgb(playful), root.winfo_rgb(bg)) >= 4.5
            # the hint lies between the background and the text
            fg = luminance(root, widget["foreground"])
            hint = luminance(root, tags["placeholder"])
            assert min(fg, luminance(root, bg)) < hint < max(fg, luminance(root, bg))


OWN_TAGS = {"system_text", "loading_text", "placeholder"}  # not from the syntax theme


def test_idle_classic_comments_do_not_make_the_panel_red(panel_view, root, monkeypatch):
    """Jakub 2026-11-26: the hint and the playful lines took the comment colour, red (#dd0000)
    in IDLE Classic; red is for errors only."""
    options = builtin_syntax_themes(monkeypatch)["IDLE Classic"]
    assert options["comment"]["foreground"] == "#dd0000"

    _, tags, _ = panel_view.theme_colors(UI_TEXTS["Windows"], options, root.winfo_rgb)

    for tag in OWN_TAGS:
        r, g, b = (c // 257 for c in root.winfo_rgb(tags[tag]))
        assert not (r > 150 and g < 100 and b < 100), (tag, tags[tag])
    r, g, b = (c // 257 for c in root.winfo_rgb(tags["placeholder"]))
    assert max(r, g, b) - min(r, g, b) < 10  # grey: black text into a white background
    assert tags["error_text"] == options["stderr"]["foreground"]  # errors stay red


def test_playful_blue_follows_a_blue_background(panel_view, root):
    """On a blue background the blue is made lighter until it is readable."""
    bg = "#2a4a8a"
    options = {"TEXT": {"foreground": "#ffffff", "background": bg}}
    rgb = root.winfo_rgb
    assert panel_view.contrast(rgb(panel_view.PLAYFUL_BLUE[True]), rgb(bg)) < 4.5

    _, tags, _ = panel_view.theme_colors({}, options, rgb)

    playful = tags["system_text"]
    assert panel_view.contrast(rgb(playful), rgb(bg)) >= 4.5
    r, g, b = rgb(playful)
    assert b > r and b > g  # still blue


def test_dark_ui_with_dark_syntax_theme_uses_its_colours(panel_view, root, monkeypatch):
    options = builtin_syntax_themes(monkeypatch)["Default Dark"]

    widget, tags, border = panel_view.theme_colors(UI_TEXTS["Clean Dark"], options, root.winfo_rgb)

    assert widget["background"] == "#2d2d2d"
    assert widget["foreground"] == widget["insertbackground"] == "#9f9f9f"
    assert widget["selectbackground"] == options["sel"]["background"]
    assert tags["assistant_header"] == options["string"]["foreground"]
    assert tags["error_text"] == options["stderr"]["foreground"]
    # the border lies between text and background
    assert luminance(root, "#2d2d2d") < luminance(root, border) < luminance(root, "#9f9f9f")


def test_dark_ui_with_light_syntax_theme_stays_dark(panel_view, root, monkeypatch):
    """Thonny does not tie the two themes: the light syntax colours would be unreadable."""
    options = builtin_syntax_themes(monkeypatch)["Default Light"]

    widget, tags, _ = panel_view.theme_colors(UI_TEXTS["Clean Dark"], options, root.winfo_rgb)

    assert widget["background"] == "#2d2d2d"
    assert {tag: tags[tag] for tag in panel_view.TAG_COLORS} == panel_view.FALLBACK_COLORS[True]
    assert "selectbackground" not in widget


def test_light_ui_keeps_the_colours_of_beta_1_with_a_dark_syntax_theme(panel_view, root, monkeypatch):
    options = builtin_syntax_themes(monkeypatch)["Default Dark"]

    widget, tags, _ = panel_view.theme_colors(UI_TEXTS["Windows"], options, root.winfo_rgb)

    assert widget["background"] == "SystemWindow"
    assert {tag: tags[tag] for tag in panel_view.TAG_COLORS} == panel_view.FALLBACK_COLORS[False]


def test_without_ui_text_style_the_syntax_theme_decides(panel_view, root):
    options = {"TEXT": {"foreground": "#eeeeee", "background": "#111111"}}

    widget, tags, border = panel_view.theme_colors({}, options, root.winfo_rgb)

    assert widget == {
        "background": "#111111",
        "foreground": "#eeeeee",
        "insertbackground": "#eeeeee",
    }
    # the theme has no tag colours
    assert {tags[tag] for tag in panel_view.TAG_COLORS} == {"#eeeeee"}
    assert border == "#696969"  # 0.4 * 0xee + 0.6 * 0x11 = 105
    assert tags["placeholder"] == "#747474"  # 0.45 * 0xee + 0.55 * 0x11 = 116


def test_no_theme_colours_leave_tk_defaults(panel_view, root):
    assert panel_view.theme_colors({}, {}, root.winfo_rgb) == ({}, {}, None)


def test_panel_follows_a_theme_change(panel_view, root, fake_thonny, monkeypatch):
    from tkinter import ttk

    from thonny import codeview

    themes = builtin_syntax_themes(monkeypatch)
    style = ttk.Style(root)
    style.configure("Text", **UI_TEXTS["Clean Sepia"])
    monkeypatch.setattr(codeview, "_syntax_options", themes["Default Light"])
    view = panel_view.AIAView(root)
    try:
        assert view._messages_text["background"] == "#F7F6F0"
        assert view._input_text["background"] == "#F7F6F0"

        # Workbench.reload_themes: a new UI theme, then the syntax theme and this event
        style.configure("Text", **UI_TEXTS["Clean Dark"])
        monkeypatch.setattr(codeview, "_syntax_options", themes["Default Dark"])
        fake_thonny.queue_event("SyntaxThemeChanged")

        dark = themes["Default Dark"]
        assert view._messages_text["background"] == "#2d2d2d"
        assert view._messages_text["foreground"] == "#9f9f9f"
        assert view._input_text["background"] == "#2d2d2d"
        assert view._input_text["insertbackground"] == "#9f9f9f"
        assert (
            view._messages_text.tag_cget("assistant_header", "foreground")
            == dark["string"]["foreground"]
        )
    finally:
        view.destroy()
        style.configure("Text", background="", foreground="")
    assert view._apply_theme not in fake_thonny.handlers.get("SyntaxThemeChanged", [])


def test_input_hint_shows_while_the_input_is_empty_and_unfocused(panel_view, root, monkeypatch):
    """Jakub 2026-10-13 (#74): a grey hint in the empty input, never sent. Here because it
    needs a real Tk interpreter, like the theme tests."""
    from thonny import codeview

    themes = builtin_syntax_themes(monkeypatch)
    monkeypatch.setattr(codeview, "_syntax_options", themes["Default Light"])
    view = panel_view.AIAView(root)
    view._known_problem = lambda signature=None: None  # the assistant can answer
    view._update_input()  # the fake has no provider: the input started off
    sent = []
    view._request_llm_response = lambda: sent.append(list(view._conversation))
    content = lambda: view._input_text.get("1.0", "end-1c")
    try:
        assert content() == panel_view.input_hint_text()
        assert view._input_text.tag_cget("placeholder", "foreground")
        view._on_send()  # Send with only the hint in the input
        assert sent == []

        view._hide_placeholder()  # <FocusIn>
        assert content() == ""
        view._input_text.insert("1.0", "Ahoj")
        view._show_placeholder()  # <FocusOut> with a message typed: it stays
        assert content() == "Ahoj"
        view._on_send()
        assert sent[0][-1]["content"] == "Ahoj"

        view._show_placeholder()  # empty again, the focus elsewhere
        assert content() == panel_view.input_hint_text()
    finally:
        view.destroy()
