from oracle.extract import discover, extract, stable_name


def test_keeps_copy_and_flags_drops_noise():
    js = ('checkGate("labs_voice_notes");a="flex items-center gap-2";b="https://x.com/a.js";'
          'c="Record a voice note and we transcribe it.";d={defaultMessage:"Voice notes"};'
          'e="Something went wrong. Please try again.";f="aria-label";g="show_billing_page";h="x=>{return 1}"')
    out = extract(js)
    assert out["labs_voice_notes"].kind == "flag"
    assert out["Record a voice note and we transcribe it."].kind == "copy"
    assert out["Voice notes"].kind == "copy"          # i18n defaultMessage, even when short
    assert out["show_billing_page"].kind == "key"
    for noise in ("flex items-center gap-2", "https://x.com/a.js", "aria-label", "x=>{return 1}"):
        assert noise not in out


def test_unicode_and_single_quotes_decode():
    out = extract("a='Don\\'t lose your work.';b=\"Here\\u2019s what\\u2019s new.\"")
    assert "Don't lose your work." in out
    assert "Here’s what’s new." in out


def test_discover_vite_and_webpack_chunks():
    vite = 'm.f=["assets/settings-Qw12Er34.js"];import("./other-Ab12Cd34.js")'
    found = discover(vite, "https://cdn.app.com/assets/index-Zz98Yy76.js")
    assert "https://cdn.app.com/assets/settings-Qw12Er34.js" in found
    assert "https://cdn.app.com/assets/other-Ab12Cd34.js" in found

    runtime = 'r.u=function(e){return"static/chunks/"+({77:"lazy-pane"}[e]||e)+"."+{77:"aa11bb22cc33dd44",88:"ee55ff66aa77bb88"}[e]+".js"}'
    found = discover(runtime, "https://app.com/_next/static/chunks/webpack-1a2b3c4d5e6f7a8b.js")
    assert "https://app.com/_next/static/chunks/lazy-pane.aa11bb22cc33dd44.js" in found
    assert "https://app.com/_next/static/chunks/88.ee55ff66aa77bb88.js" in found


def test_stable_name_strips_content_hash():
    assert stable_name("https://cdn.x.com/assets/index-Ab12Cd34.js") == stable_name("https://cdn.x.com/assets/index-Zz98Yy76.js")
    assert stable_name("https://x.com/_next/static/chunks/framework-2c79e2a64abdb08b.js") == "x.com/_next/static/chunks/framework.js"
    assert stable_name("https://x.com/app/main.js") == "x.com/app/main.js"


def test_template_literals_and_dashed_hashes():
    out = extract("a=`Invite your team to this project`;b=`Hi ${name}, welcome`")
    assert "Invite your team to this project" in out
    assert not any("welcome" in s for s in out)  # interpolated templates are skipped
    assert stable_name("https://s.x.com/a/ActivityHistoryComponents.C2dlNX-o.js") == "s.x.com/a/ActivityHistoryComponents.js"
    assert stable_name("https://x.com/a/use-chat-state.js") == "x.com/a/use-chat-state.js"
