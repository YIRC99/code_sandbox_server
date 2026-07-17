import argparse
import re
from pathlib import Path

import yaml

IMAGE_TAG_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


def set_image_tag(path: Path, image_name: str, image_tag: str) -> None:
    if IMAGE_TAG_PATTERN.fullmatch(image_tag) is None:
        raise ValueError(f"Invalid container image tag: {image_tag!r}")

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    images = document.get("images", []) if isinstance(document, dict) else []
    matches = [image for image in images if image.get("name") == image_name]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one Kustomize image named {image_name!r}")

    matches[0]["newTag"] = image_tag
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    try:
        set_image_tag(args.file, args.image, args.tag)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
