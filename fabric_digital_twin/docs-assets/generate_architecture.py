from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs-assets"
SOURCE = ASSETS / "architecture.mmd"
CONFIG = ASSETS / "mermaid-config.json"
OUTPUT = ASSETS / "fig-architecture.png"
README = ROOT / "README.md"
DOCS = ROOT / "docs"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def render_diagram() -> None:
    renderer = shutil.which("mmdc")
    if renderer:
        command = [renderer, "-i", str(SOURCE), "-o", str(OUTPUT)]
        if os.name == "nt" and renderer.lower().endswith((".cmd", ".bat")):
            npx_args = ["--yes", "@mermaid-js/mermaid-cli", *command[1:]]
            command = npx_command(npx_args)
    else:
        command = npx_command(["--yes", "@mermaid-js/mermaid-cli", "-i", str(SOURCE), "-o", str(OUTPUT)])

    command.extend(["--configFile", str(CONFIG), "--backgroundColor", "#F5F8FC", "--scale", "2"])
    chrome = next(
        (
            path
            for path in (
                os.environ.get("PUPPETEER_EXECUTABLE_PATH"),
                shutil.which("chrome"),
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            )
            if path and Path(path).is_file()
        ),
        None,
    )

    with tempfile.TemporaryDirectory(prefix="fabric-digital-twin-") as temp_dir:
        if chrome:
            config_path = Path(temp_dir) / "puppeteer.json"
            config_path.write_text(
                json.dumps({"executablePath": chrome, "headless": True, "args": ["--no-sandbox"]}),
                encoding="utf-8",
            )
            command.extend(["--puppeteerConfigFile", str(config_path)])
        subprocess.run(command, cwd=ROOT, check=True)


def npx_command(arguments: list[str]) -> list[str]:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if not npx:
        raise RuntimeError("Mermaid CLI is unavailable; install @mermaid-js/mermaid-cli or add mmdc to PATH.")
    if os.name != "nt" or not npx.lower().endswith((".cmd", ".bat")):
        return [npx, *arguments]

    node = shutil.which("node")
    if node:
        npx_cli = Path(node).parent / "node_modules" / "npm" / "bin" / "npx-cli.js"
        if npx_cli.is_file():
            return [node, str(npx_cli), *arguments]
    raise RuntimeError("Could not locate npm's npx JavaScript entrypoint to run Mermaid CLI.")


def sync_readme_diagram() -> None:
    start_marker = "<!-- architecture-diagram:start -->"
    end_marker = "<!-- architecture-diagram:end -->"
    readme = README.read_text(encoding="utf-8")
    start = readme.find(start_marker)
    end = readme.find(end_marker)
    if start < 0 or end < start:
        raise ValueError(f"README must contain {start_marker} and {end_marker}")

    diagram = SOURCE.read_text(encoding="utf-8").rstrip()
    updated = readme[:start + len(start_marker)] + "\n```mermaid\n" + diagram + "\n```\n" + readme[end:]
    README.write_text(updated, encoding="utf-8", newline="\n")


def image_relationship(package: ZipFile, docx_path: Path) -> tuple[str, str]:
    document = ET.fromstring(package.read("word/document.xml"))
    relationships = ET.fromstring(package.read("word/_rels/document.xml.rels"))
    targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
    paragraphs = list(document.iter(f"{{{W_NS}}}p"))
    start = next(
        (i for i, paragraph in enumerate(paragraphs)
         if "3. Solution Architecture" in "".join(t.text or "" for t in paragraph.iter(f"{{{W_NS}}}t"))
         or "3. Arsitektur Solusi" in "".join(t.text or "" for t in paragraph.iter(f"{{{W_NS}}}t"))),
        None,
    )
    if start is None:
        raise ValueError(f"Architecture section not found in {docx_path.name}")

    for paragraph in paragraphs[start + 1:]:
        text = "".join(t.text or "" for t in paragraph.iter(f"{{{W_NS}}}t")).strip()
        if text.startswith(("3.1 ", "4. Prerequisites", "4. Prasyarat")):
            break
        for image_ref in paragraph.iter(f"{{{A_NS}}}blip"):
            relation_id = image_ref.attrib.get(f"{{{R_NS}}}embed")
            if relation_id in targets:
                target = targets[relation_id]
                return str(PurePosixPath("word") / target.lstrip("/")), relation_id

    raise ValueError(f"Architecture image not found in {docx_path.name}")


def update_image_extent(document_xml: bytes, relation_id: str, width: int, height: int) -> bytes:
    paragraph_pattern = re.compile(rb"<w:p(?:\s[^>]*)?>.*?</w:p>", re.DOTALL)
    relation = f'r:embed="{relation_id}"'.encode()
    extent_pattern = re.compile(rb'(<wp:extent\b[^>]*\bcx=")(\d+)("\s+cy=")(\d+)(")')
    drawing_extent_pattern = re.compile(rb'(<a:ext\b[^>]*\bcx=")(\d+)("\s+cy=")(\d+)(")')
    for match in paragraph_pattern.finditer(document_xml):
        paragraph = match.group()
        if relation not in paragraph:
            continue
        extent_match = extent_pattern.search(paragraph)
        drawing_match = drawing_extent_pattern.search(paragraph)
        if extent_match is None or drawing_match is None:
            raise ValueError(f"Could not find image dimensions for {relation_id}")
        width_emu = int(extent_match.group(2))
        height_emu = round(width_emu * height / width)
        paragraph, wp_count = extent_pattern.subn(
            lambda item: item.group(1) + str(width_emu).encode() + item.group(3) + str(height_emu).encode() + item.group(5),
            paragraph,
            count=1,
        )
        paragraph, drawing_count = drawing_extent_pattern.subn(
            lambda item: item.group(1) + str(width_emu).encode() + item.group(3) + str(height_emu).encode() + item.group(5),
            paragraph,
            count=1,
        )
        if wp_count != 1 or drawing_count != 1:
            raise ValueError(f"Could not update image dimensions for {relation_id}")
        return document_xml[:match.start()] + paragraph + document_xml[match.end():]
    raise ValueError(f"Image paragraph for {relation_id} not found")


def image_dimensions(png: bytes) -> tuple[int, int]:
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise ValueError("Mermaid CLI did not produce a valid PNG architecture diagram.")
    return struct.unpack(">II", png[16:24])


def replace_architecture_image(docx_path: Path, image_bytes: bytes, width: int, height: int) -> None:
    with ZipFile(docx_path, "r") as source:
        target, relation_id = image_relationship(source, docx_path)
        entries = []
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "word/document.xml":
                content = update_image_extent(content, relation_id, width, height)
            entries.append((item, content))

    fd, temp_name = tempfile.mkstemp(prefix=f"{docx_path.stem}-", suffix=".docx", dir=docx_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    replaced = False
    try:
        with ZipFile(temp_path, "w", compression=ZIP_DEFLATED) as output:
            for item, content in entries:
                if item.filename == target:
                    content = image_bytes
                    replaced = True
                output.writestr(item, content)
        if not replaced:
            raise ValueError(f"Architecture image target {target} not found in {docx_path.name}")
        os.replace(temp_path, docx_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Mermaid source not found: {SOURCE}")
    render_diagram()
    image_bytes = OUTPUT.read_bytes()
    width, height = image_dimensions(image_bytes)
    sync_readme_diagram()

    documents = sorted(DOCS.glob("*.docx"))
    if not documents:
        raise FileNotFoundError(f"No Word documents found under {DOCS}")
    for docx_path in documents:
        replace_architecture_image(docx_path, image_bytes, width, height)
        print(f"Updated architecture in {docx_path.name}")
    print(f"Rendered {SOURCE.relative_to(ROOT)} to {OUTPUT.relative_to(ROOT)} ({width}x{height})")


if __name__ == "__main__":
    main()
