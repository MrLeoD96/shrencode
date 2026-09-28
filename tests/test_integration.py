import json
import subprocess

from conftest import needs_ffmpeg

from shrencode import menu
from shrencode.cli import main


def test_nss_string_escapes_expressions():
    assert menu.nss_string("C:\\Users\\me@work\\x.exe") == "'C:\\Users\\me@@work\\x.exe'"


def test_menu_icon_is_themed_svg_image():
    nss = menu.render(("shrencode", ""), ("shrencodew", ""))
    assert "$svg_shrencode = image.svg('<svg " in nss
    assert 'fill="@image.color1"' in nss
    assert "@@image" not in nss
    assert nss.count("image=svg_shrencode") == 2


def test_menu_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        menu, "launchers", lambda: (("C:\\s\\shrencode.exe", ""), ("C:\\s\\shrencodew.exe", ""))
    )
    original = b"\xef\xbb\xbfsettings{}\r\nimport 'imports/theme.nss'\r\n"
    (tmp_path / "shell.nss").write_bytes(original)
    menu.install(tmp_path)
    menu.install(tmp_path)
    text = (tmp_path / "shell.nss").read_bytes()
    assert text.count(b"shrencode.nss") == 1
    assert text.endswith(b"import 'imports/shrencode.nss'\r\n")
    nss = (tmp_path / "imports" / "shrencode.nss").read_text()
    assert "cmd='C:\\s\\shrencodew.exe' args='compare @sel(true)'" in nss
    assert "where=sel.count==2" in nss
    menu.uninstall(tmp_path)
    assert (tmp_path / "shell.nss").read_bytes() == original
    assert not (tmp_path / "imports" / "shrencode.nss").exists()


def _probe_tags(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format_tags:stream_tags=ENCODER_SETTINGS:stream=codec_name,field_order",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


@needs_ffmpeg
def test_encode_prores(clips, tmp_path, capsys):
    code = main(
        [
            "encode",
            str(clips["prores"]),
            "--output-dir",
            str(tmp_path),
            "--preset",
            "ultrafast",
            "--no-queue",
            "--json",
            "-q",
        ]
    )
    assert code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]["status"] == "done"
    assert records[-1]["type"] == "summary"
    out = tmp_path / "prores.x265.mkv"
    info = _probe_tags(out)
    assert info["format"]["tags"]["SHRENCODE_SOURCE"] == "prores.mov"
    assert "libx265" in info["streams"][0]["tags"]["ENCODER_SETTINGS"]
    assert [s["codec_name"] for s in info["streams"]] == ["hevc", "opus"]
    assert not list(tmp_path.glob(".tmp.shrencode.*"))


@needs_ffmpeg
def test_skips_lean_h264_and_existing(clips, tmp_path, capsys):
    assert main(["encode", str(clips["lean"]), "--no-queue", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0])["status"] == "skipped"


@needs_ffmpeg
def test_interlaced_is_deinterlaced_and_audio_copied(clips, tmp_path):
    assert (
        main(
            [
                "encode",
                str(clips["interlaced"]),
                "--output-dir",
                str(tmp_path),
                "--preset",
                "ultrafast",
                "--no-queue",
                "-q",
            ]
        )
        == 0
    )
    info = _probe_tags(tmp_path / "interlaced.x265.mkv")
    assert info["streams"][0]["field_order"] == "progressive"
    assert info["streams"][1]["codec_name"] == "mp2"
    assert main(["encode", str(clips["interlaced"]), "--output-dir", str(tmp_path), "--no-queue", "-q"]) == 0
