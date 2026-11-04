"""Input operator: komentar, file kosong, inline menang."""

from wth_ai.cli.inputs import read_instruction, read_targets


def test_targets_ignore_comments_and_blanks(tmp_path):
    path = tmp_path / "targets.txt"
    path.write_text("# lab\n172.28.0.10\n\n  example.test  # staging\n")
    assert read_targets(path) == ["172.28.0.10", "example.test"]


def test_empty_file_gives_empty_list(tmp_path):
    path = tmp_path / "targets.txt"
    path.write_text("\n\n# hanya komentar\n")
    assert read_targets(path) == []


def test_inline_instruction_wins(tmp_path):
    path = tmp_path / "prompt.txt"
    path.write_text("dari file")
    assert read_instruction(path, "dari cli") == "dari cli"
    assert read_instruction(path, None) == "dari file"
    assert read_instruction(None, None) == ""