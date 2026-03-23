#!/usr/bin/env python3
"""
Download a Ghidra release zip from the GitHub releases for a given GHIDRA_VERSION.

Environment variables:
- GHIDRA_VERSION (required): version string to match (e.g. "10.2.2" or "10.2")
- GITHUB_TOKEN (optional): GitHub token to increase API rate limits
- OUTPUT_DIR (optional): directory to save the downloaded zip (default: current dir)
- GHIDRA_REPO (optional): GitHub repo to query (default: NationalSecurityAgency/ghidra)
"""
import requests
import logging
from requests import (
    Session,
)
from tempfile import (
    TemporaryDirectory,
)
from pathlib import (
    Path,
)
from zipfile import (
    ZipFile,
)
from shutil import (
    move,
)
from typing import (
    List,
    Dict,
    Any,
)
from json import (
    dumps,
)
from os import (
    environ as ENVIRONMENT,
)


def main() -> None:
    logging.basicConfig(
        level=get_log_level(),
        format="[%(levelname)s] %(message)s"
    )

    try:
        version = get_string("GHIDRA_VERSION")
        with open_session() as session:
            releases = get_all_releases(session)
            release = find_release(releases, version)
            asset = find_zip_asset(release)
            
            ghidra_home = Path(get_string("OUTPUT_DIR", default="./.tools")).resolve() / "ghidra"
            ghidra_home.mkdir(parents=True, exist_ok=True)
            path = download_asset(session, asset, ghidra_home)
            unzip_asset(path)
            append_outputs(
                ghidra_home=ghidra_home,
            )
    except BaseException as e:
        raise BaseException(f"Error in setup-ghidra action") from e

def unzip_asset(asset: Path):
    try:
        parent = asset.parent
        with TemporaryDirectory(prefix="ghidra-unzip-", dir=parent) as tmpdir:
            tmpdir = Path(tmpdir)
            with ZipFile(asset, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)
                logging.debug(f"Extracted zip asset to temporary directory {tmpdir}")
            
            items = [i for i in tmpdir.iterdir()]
            if len(items) != 1:
                raise ValueError(f"Expected exactly one item in the zip archive, found {len(items)} items {[str(i) for i in items]}")
            item = items[0]
            if not item.is_dir():
                raise ValueError(f"Expected a single directory in the zip archive, found a file {item}")
            
            for sub_item in item.iterdir():
                dest_path = parent / sub_item.name
                move(sub_item, dest_path)
                    
        asset.unlink()
        if get_log_level() <= logging.DEBUG:
            logging.debug(f"Asset {asset.resolve()} contents")
            for i in parent.iterdir():
                logging.debug(str(i))
    except BaseException as e:
        raise BaseException("Failed to unzip asset") from e

def find_zip_asset(release: Dict[str, Any]) -> Dict[str, Any]:
    try:
        for a in release.get("assets", []):
            name = (a.get("name") or "").lower()
            if name.endswith('.zip') or '.zip' in name:
                return a
        raise ValueError(f"No zip asset found in release {release.get('name')}")
    except BaseException as e:
        raise BaseException("Failed to find zip asset in release") from e

def download_asset(session: requests.Session, asset: Dict[str, Any], out_dir: Path) -> Path:
    logging.info("Downloading asset %s", asset.get("name"))
    try:
        url = asset.get("browser_download_url")
        if not url:
            raise ValueError("Asset is missing 'browser_download_url'")
        
        out_path = out_dir / "ghidra.zip"
        with session.get(url, stream=True) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:    
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk: f.write(chunk)
        return Path(out_path)
    except BaseException as e:
        raise BaseException("Failed to download asset") from e


def find_release(releases: List[Dict[str, Any]], version: str) -> Dict[str, Any]:
    try:
        length = len(releases)
        if length == 0:
            raise ValueError("No releases to be filtered")
        
        if version.lower() == "latest":
            return releases[0]
        
        version = f"Ghidra {version}"
        for r in releases:
            name = (r.get("name") or "")
            if name == version:
                return r
        raise ValueError(f"No release matching name '{version}' found")
    except BaseException as e:
        raise BaseException("Failed to find release") from e


def get_all_releases(session: requests.Session) -> List[Dict[str, Any]]:
    logging.info("Querying for Ghidra releases")
    try:
        url = f"https://api.github.com/repos/NationalSecurityAgency/ghidra/releases"
        resp = session.get(url)
        resp.raise_for_status()
        out = resp.json()
        try:
            if get_log_level() <= logging.DEBUG:
                logging.debug("Query for releases found %d releases", len(out))
                for i in range(len(out)):
                    logging.debug(f"{i}. Release: {dumps(out[i])}")
        except: pass #fmt: off #fmt: on
        return out

    except BaseException as e:
        raise BaseException("Failed to fetch releases from GitHub API") from e
        

def open_session():
    try:
        logging.debug("Opening HTTP session")
        session = Session()
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ghidra-downloader"
        }
        token = get_string("GITHUB_TOKEN")
        headers["Authorization"] = f"token {token}"
        session.headers.update(headers)
        logging.debug("HTTP session opened successfully")
        return session
    except BaseException as e:
        raise BaseException(f"Failed to create HTTP session") from e


def get_string(key:str, *, default: str|None = None) -> str:
    if key in ENVIRONMENT:
        return ENVIRONMENT[key]
    if default is not None:
        return default
    raise ValueError(f"Environment variable '{key}' is not found and no default value provided")

def get_int(key:str, *, default: int|None = None) -> int:
    s = get_string(key, default=str(default) if default is not None else None)
    try:
        return int(s)
    except ValueError as e:
        raise ValueError(f"Environment variable '{key}' value '{s}' cannot be converted to int") from e
    
def get_log_level() -> int:
    key = "LOG_LEVEL"
    s = get_string(key, default="default")
    if s.lower() == "default":
        return logging.INFO
    level = getattr(logging, s.upper(), None)
    if isinstance(level, int):
        return level
    raise ValueError(f"Environment variable '{key}' value '{s}' is not a valid log level")

def append_outputs(**kwargs: Any) -> None:
    with open(get_string(f"GITHUB_OUTPUT"), "a") as file:
        for k, v in kwargs.items():
            key = k.replace(".", "_").upper()
            value = str(v).replace("\n", "%0A").replace("\r", "%0D")
            logging.debug(f"Appending output '{key}'='{value}'")
            file.write(f"{key}={value}\n")

if __name__ == '__main__':
    main()
