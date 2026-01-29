import argparse
import os
import zipfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a zip with normalized forward-slash paths."
    )
    parser.add_argument("--dist", required=True, help="Path to dist folder")
    parser.add_argument("--zip", required=True, help="Output zip path")
    parser.add_argument(
        "--root-name",
        required=True,
        help="Top-level folder name inside the zip",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dist_path = os.path.abspath(args.dist)
    zip_path = os.path.abspath(args.zip)
    root_name = args.root_name.strip("/\\")

    if not os.path.isdir(dist_path):
        raise SystemExit(f"Dist folder not found: {dist_path}")
    if not root_name:
        raise SystemExit("Root name must be non-empty")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(dist_path):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, dist_path)
                arc_name = f"{root_name}/{rel_path.replace(os.sep, '/')}"
                zf.write(full_path, arc_name)


if __name__ == "__main__":
    main()
