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
import sys
import os

from requests import (
    Session,
)
from typing import (
    Optional,
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


def download_asset(session: requests.Session, asset: Dict[str, Any], out_dir: str) -> str:
    url = asset.get("browser_download_url")
    filename = asset.get("name")
    out_path = os.path.join(out_dir, filename)
    with session.get(url, stream=True) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    return out_path


def main() -> None:
    logging.basicConfig(
        level=get_log_level(),
        format="[%(levelname)s] %(message)s"
    )

    try:
        version = get_string("GHIDRA_VERSION")
        out_dir = get_string("OUTPUT_DIR", default=".")
        with open_session() as session:
            releases = get_all_releases(session)
            release: Optional[Dict[str, Any]] = find_release(releases, version)
            if not release:
                logging.error("No release matching version '%s' found in NationalSecurityAgency/ghidra", version)
                sys.exit(3)

            logging.info("Found release: %s", release.get("tag_name"))

            # find a zip asset
            asset: Optional[Dict[str, Any]] = None
            for a in release.get("assets", []):
                name = (a.get("name") or "").lower()
                if name.endswith('.zip') or '.zip' in name:
                    asset = a
                    break

            if not asset:
                logging.error("No zip asset found in release %s", release.get("tag_name"))
                sys.exit(4)

            logging.info("Downloading asset %s", asset.get("name"))
            os.makedirs(out_dir, exist_ok=True)
            try:
                path = download_asset(session, asset, out_dir)
            except Exception as e:
                logging.error("Download failed: %s", e)
                sys.exit(5)

            logging.info("Downloaded to %s", path)
    except BaseException as e:
        raise BaseException(f"Error in setup-ghidra action") from e


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

if __name__ == '__main__':
    main()
