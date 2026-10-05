# /config/beetsplug/fetchart_extra.py

import os
import subprocess
import requests
import imghdr
import shutil
import hashlib
from beets.plugins import BeetsPlugin
from beets.util import bytestring_path
from requests.exceptions import RequestException


class FetchArtExtraPlugin(BeetsPlugin):
    def __init__(self):
        super(FetchArtExtraPlugin, self).__init__()
        self.config.add({
            'fanarttv_key': None,
            'theaudiodb_key': None,
            'sources': ['fanarttv', 'theaudiodb', 'musicbrainz'],
            'types': ['discart', 'spine', 'back'],
            'run_on_import': True,
            'resize': {
                'enabled': True,
                'discart': ['1000', '1000'],
                'back': ['750', '750'],
                'spine': ['35', '700'],
            },
            'fuzz': '15%',
            'background': {
                'multi_corner': True
            }
        })

        # Detect ImageMagick binary (IMv7 uses `magick`)
        self._magick_bin = ["magick"] if shutil.which("magick") else ["convert"]

        if self.config['run_on_import'].get(bool):
            self.import_stages = [self.import_stage]

    # -------------------------
    # Import stage
    # -------------------------
    def import_stage(self, session, task):
        album = task.album
        if album:
            self._process_album(album, pretend=False)

    # -------------------------
    # Manual command
    # -------------------------
    def commands(self):
        from beets.ui import Subcommand

        cmd = Subcommand('fetchart_extra', help='fetch extra artworks (discart, spine, back)')
        cmd.parser.add_option(
            "--pretend", action="store_true", default=False,
            help="show what would be done, but don't download or save"
        )

        def func(lib, opts, args):
            pretend = opts.pretend
            for album in lib.albums(args):
                self._process_album(album, pretend=pretend)

        cmd.func = func
        return [cmd]

    # -------------------------
    # Album processor
    # -------------------------
    def _process_album(self, album, pretend=False):
        mbid = album.mb_albumid
        if not mbid:
            self._log.debug(f"No MBID found for {album}, skipping artwork fetch.")
            return

        album_dir = album.path.decode("utf-8")

        for art_type in self.config['types'].get():
            if self._art_exists(album_dir, art_type):
                self._log.debug(f"{art_type} already exists for {album}")
                continue

            for source in self.config['sources'].get():
                if pretend:
                    self._log.debug(f"[pretend] Would try {art_type} from {source} for {album}")
                    continue

                if source == 'fanarttv':
                    image_bytes, ext = self._fetch_from_fanarttv(mbid, art_type)
                elif source == 'musicbrainz':
                    image_bytes, ext = self._fetch_from_musicbrainz(mbid, art_type)
                elif source == 'theaudiodb':
                    image_bytes, ext = self._fetch_from_theaudiodb(album, art_type)
                else:
                    continue

                # Handle multi-discart lists
                if isinstance(image_bytes, list) and art_type == "discart":
                    total = len(image_bytes)
                    for idx, content, ext in image_bytes:
                        suffix = "" if total == 1 and idx == 1 else str(idx)
                        filename = f"discart{suffix}.png"
                        out_path = os.path.join(album_dir, filename)
                        out_path = self._save_image(out_path, content, source, f"discart{suffix}")
                        if out_path:
                            self._fix_discart_background(out_path)
                            if self.config['resize']['enabled'].get(bool):
                                resize_cfg = self.config['resize']['discart'].as_str_seq()
                                if resize_cfg and len(resize_cfg) == 2:
                                    w, h = map(int, resize_cfg)
                                    self._resize_image(out_path, w, h)
                            self._log.debug(f"✔ {os.path.basename(out_path)} ready for {album} (from {source})")
                    break

                elif image_bytes:
                    out_path = os.path.join(album_dir, f"{art_type}.png")
                    out_path = self._save_image(out_path, image_bytes, source, art_type)
                    if not out_path:
                        self._log.debug(f"→ {source} failed for {art_type}, trying next source…")
                        continue

                    if art_type.startswith("discart"):
                        self._fix_discart_background(out_path)

                    if self.config['resize']['enabled'].get(bool):
                        if art_type in self.config['resize']:
                            resize_cfg = self.config['resize'][art_type].as_str_seq()
                            if resize_cfg and len(resize_cfg) == 2:
                                w, h = map(int, resize_cfg)
                                self._resize_image(out_path, w, h)

                    self._log.debug(f"✔ {art_type} ready for {album} (from {source})")
                    break
            else:
                self._log.warning(f"⚠ No valid {art_type} found for {album}")

    # -------------------------
    # Helpers
    # -------------------------
    def _art_exists(self, album_dir, art_type):
        for ext in ('.png', '.jpg', '.jpeg'):
            if os.path.exists(os.path.join(album_dir, f"{art_type}{ext}")):
                return True
        return False

    def _file_checksum(self, path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _save_image(self, path, image_bytes, source=None, art_type=None):
        fmt = imghdr.what(None, h=image_bytes)
        if not fmt:
            head = image_bytes[:50].decode("latin-1", "ignore").strip().replace("\n", " ")
            self._log.warning(f"⚠ {source} did not return a valid {art_type} image (starts with {head!r})")
            return None

        if fmt == "jpeg":
            fmt = "jpg"

        final_fmt = "png"
        base, _ = os.path.splitext(path)
        path = f"{base}.{final_fmt}"
        tmp_path = f"{path}.tmp"

        with open(bytestring_path(tmp_path), 'wb') as f:
            f.write(image_bytes)

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            self._log.warning(f"⚠ {source} returned empty data for {art_type}, skipping.")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return None

        new_hash = self._file_checksum(tmp_path)
        if os.path.exists(path):
            existing_hash = self._file_checksum(path)
            if new_hash == existing_hash:
                self._log.debug(f"→ Skipping {art_type}, identical file already exists: {path}")
                os.remove(tmp_path)
                return None

        try:
            self._run_magick([tmp_path, path])
            os.remove(tmp_path)
        except Exception:
            os.replace(tmp_path, path)

        return path

    def _run_magick(self, args):
        return subprocess.run(self._magick_bin + args, check=True)

    def _fix_discart_background(self, path):
        fuzz = self.config['fuzz'].get(str) or "15%"
        multi_corner = self.config['background']['multi_corner'].get(bool)

        try:
            if multi_corner:
                self._run_magick([
                    path,
                    "-alpha", "set",
                    "-fuzz", fuzz,
                    "-fill", "none",
                    "-draw", "alpha 0,0 floodfill",
                    "-draw", "alpha 0,%[fx:h-1] floodfill",
                    "-draw", "alpha %[fx:w-1],0 floodfill",
                    "-draw", "alpha %[fx:w-1],%[fx:h-1] floodfill",
                    "-draw", "alpha %[fx:w/2],%[fx:h/2] floodfill",  # center
                    path
                ])
                self._log.debug(f"→ Removed white background (multi-corner+center) with fuzz={fuzz}: {path}")
            else:
                self._run_magick([
                    path,
                    "-alpha", "set",
                    "-fuzz", fuzz,
                    "-fill", "none",
                    "-draw", "alpha 0,0 floodfill",
                    path
                ])
                self._log.debug(f"→ Removed white background (single-corner) with fuzz={fuzz}: {path}")
        except Exception as e:
            self._log.warning(f"⚠ Background removal failed on {path}: {e}")

    def _resize_image(self, path, width, height):
        try:
            self._run_magick([path, "-resize", f"{width}x{height}!", path])
            self._log.debug(f"→ Resized {path} to {width}x{height}")
        except Exception as e:
            self._log.warning(f"⚠ Resize failed on {path}: {e}")

    # ------------------------------
    # Source fetchers
    # ------------------------------
    def _fetch_from_fanarttv(self, mbid, art_type):
        key = self.config['fanarttv_key'].get()
        if not key:
            return None, None
        url = f"https://webservice.fanart.tv/v3/music/{mbid}?api_key={key}"
        try:
            r = requests.get(url, timeout=15)
            if not r.ok:
                self._log.warning(f"⚠ FanartTV request failed with {r.status_code} for {mbid}")
                return None, None
            data = r.json()
            mapping = {"discart": "cdart", "spine": "albumspine", "back": "albumcoverback"}
            key = mapping.get(art_type)
            if key and key in data:
                try:
                    img_url = data[key][0]['url']
                    ext = img_url.split('.')[-1].lower()
                    return requests.get(img_url, timeout=15).content, ext
                except RequestException as e:
                    self._log.warning(f"⚠ FanartTV fetch failed: {e}")
                    return None, None
        except RequestException as e:
            self._log.warning(f"⚠ FanartTV request error: {e}")
            return None, None
        return None, None

    def _fetch_from_musicbrainz(self, mbid, art_type):
        if art_type == "spine":
            self._log.debug(f"→ Skipping spine: MusicBrainz does not provide spine images")
            return None, None

        url = f"https://coverartarchive.org/release/{mbid}"
        try:
            r = requests.get(url, timeout=15)
            if not r.ok:
                self._log.warning(f"⚠ MusicBrainz request failed with {r.status_code} for {mbid}")
                return None, None
            data = r.json()
            for image in data.get("images", []):
                types = image.get("types", [])
                if art_type == "back" and "Back" in types:
                    img_url = image["image"]
                    ext = img_url.split('.')[-1].lower()
                    return requests.get(img_url, timeout=15).content, ext
                elif art_type == "discart" and "Medium" in types:
                    img_url = image["image"]
                    ext = img_url.split('.')[-1].lower()
                    return requests.get(img_url, timeout=15).content, ext
            self._log.debug(f"→ No {art_type} found in MusicBrainz for release {mbid}")
            return None, None
        except RequestException as e:
            self._log.warning(f"⚠ MusicBrainz fetch failed for {mbid}: {e}")
            return None, None
        except Exception as e:
            self._log.warning(f"⚠ Unexpected MusicBrainz error for {mbid}: {e}")
            return None, None

    def _fetch_from_theaudiodb(self, album, art_type):
        key = self.config['theaudiodb_key'].get()
        if not key:
            return None, None
        artist = album.albumartist
        title = album.album
        url = f"https://www.theaudiodb.com/api/v1/json/{key}/searchalbum.php?s={artist}&a={title}"
        try:
            r = requests.get(url, timeout=15)
            if not r.ok:
                self._log.warning(f"⚠ TheAudioDB request failed with {r.status_code} for {artist} - {title}")
                return None, None
            data = r.json()
            albums = data.get("album")
            if not albums:
                return None, None
            a = albums[0]
            mapping = {
                "discart": "strAlbumCDart",
                "back": "strAlbumThumbBack",
                "spine": "strAlbumSpine"
            }
            field = mapping.get(art_type)
            if field and a.get(field):
                img_url = a[field]
                ext = img_url.split('.')[-1].lower()
                return requests.get(img_url, timeout=15).content, ext
        except RequestException as e:
            self._log.warning(f"⚠ TheAudioDB fetch failed: {e}")
            return None, None
        except Exception as e:
            self._log.warning(f"⚠ Unexpected TheAudioDB error: {e}")
            return None, None
        return None, None
