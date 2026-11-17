"""Integrasi opsional dengan database utama SaaS sekolah.

Modul ini tidak melakukan apa pun saat diimpor. Kredensial dibaca dari
environment (`SAAS_API_BASE_URL`, `SAAS_API_KEY`) sehingga aplikasi tetap
berjalan normal ketika integrasi tidak digunakan sama sekali.
"""

import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

DEFAULT_BASE_URL = "https://onrender.com"
PAGE_SIZE = 100
MAX_PAGES = 50
REQUEST_TIMEOUT = 20.0

# Nilai `mode` yang diterima endpoint admin per sumber data.
STUDENT_MODES = ("all", "active")
EMPLOYEE_MODES = ("all", "by_type")


class SaasError(Exception):
    """Kesalahan integrasi yang aman ditampilkan ke admin."""


class SaasNotConfigured(SaasError):
    """SAAS_API_KEY belum diatur di environment server."""


def base_url() -> str:
    return (os.getenv("SAAS_API_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")


def api_key() -> str:
    return (os.getenv("SAAS_API_KEY") or "").strip()


def is_configured() -> bool:
    """True bila server sudah diberi kunci API SaaS."""
    return bool(api_key())


LIST_KEYS = ("data", "items", "results", "records", "rows")


def _extract_items(payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any], bool]:
    """Ambil daftar item + metadata pagination dari berbagai bentuk respons.

    Nilai ketiga (recognized) menandai apakah bentuk respons memang dikenali,
    sehingga daftar kosong yang sah bisa dibedakan dari respons tak dikenal.
    """
    meta: Dict[str, Any] = {}

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], meta, True

    if not isinstance(payload, dict):
        return [], meta, False

    for key in ("meta", "pagination", "page_info"):
        value = payload.get(key)
        if isinstance(value, dict):
            meta.update(value)

    for key in ("total", "per_page", "current_page", "last_page", "total_pages", "next_page_url"):
        if key in payload and key not in meta:
            meta[key] = payload[key]

    for key in LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)], meta, True
        if isinstance(value, dict):
            nested, nested_meta, nested_recognized = _extract_items(value)
            meta.update(nested_meta)
            if nested_recognized:
                return nested, meta, True

    return [], meta, False


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _shape_message(resource: str, payload: Any) -> str:
    """Pesan jelas saat struktur JSON API tidak dapat dibaca."""
    if isinstance(payload, dict):
        keys = ", ".join(repr(key) for key in list(payload.keys())[:8]) or "(kosong)"
    else:
        keys = type(payload).__name__
    return (
        "Bentuk respons API SaaS untuk /{} tidak dikenali. Aplikasi mengharapkan array JSON langsung "
        "atau daftar item pada salah satu field: data, items, results, records, rows. "
        "Kunci yang diterima: {}."
    ).format(resource, keys)


def _has_next_page(meta: Dict[str, Any], page: int) -> bool:
    """Tentukan apakah masih ada halaman berikutnya.

    Bila API tidak memberi metadata pagination, pemanggil terus meminta halaman
    berikutnya sampai menerima halaman kosong atau halaman berisi duplikat saja;
    ukuran batch tidak dipakai sebagai penanda halaman terakhir.
    """
    last_page = _as_int(meta.get("last_page") or meta.get("total_pages"))
    if last_page is not None:
        return page < last_page

    if meta.get("next_page_url"):
        return True

    total = _as_int(meta.get("total"))
    if total is not None:
        per_page = _as_int(meta.get("per_page")) or PAGE_SIZE
        return page * per_page < total

    return True


async def fetch_collection(resource: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Ambil seluruh item dari `GET /{resource}` dengan mengikuti pagination."""
    key = api_key()
    if not key:
        raise SaasNotConfigured(
            "Integrasi SaaS belum aktif: variabel environment SAAS_API_KEY belum diatur di server."
        )

    url = "{}/{}".format(base_url(), resource.lstrip("/"))
    headers = {"X-API-Key": key, "Accept": "application/json"}

    collected: List[Dict[str, Any]] = []
    seen_ids = set()
    page = 1

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            while page <= MAX_PAGES:
                query: Dict[str, Any] = dict(params or {})
                query["page"] = page
                query["per_page"] = PAGE_SIZE

                response = await client.get(url, params=query, headers=headers)
                response.raise_for_status()

                try:
                    payload = response.json()
                except ValueError:
                    raise SaasError("Respons API SaaS bukan JSON yang valid.")

                batch, meta, recognized = _extract_items(payload)
                if not recognized:
                    raise SaasError(_shape_message(resource, payload))

                if not batch:
                    break

                # Lindungi dari API yang mengabaikan parameter pagination:
                # bila isi halaman hanya berisi item yang sudah pernah dilihat, berhenti.
                fresh = [item for item in batch if str(item.get("id")) not in seen_ids]
                if not fresh:
                    break

                for item in fresh:
                    seen_ids.add(str(item.get("id")))
                    collected.append(item)

                if not _has_next_page(meta, page):
                    break

                page += 1
            else:
                raise SaasError(
                    "Pagination API SaaS belum berakhir setelah {} halaman untuk /{}. "
                    "Periksa metadata pagination pada API.".format(MAX_PAGES, resource)
                )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise SaasError(
                "API SaaS menolak kredensial (HTTP {}). Periksa kembali nilai SAAS_API_KEY.".format(status)
            )
        raise SaasError("API SaaS mengembalikan error HTTP {} untuk /{}.".format(status, resource))
    except httpx.RequestError:
        raise SaasError(
            "Tidak dapat menghubungi API SaaS di {}. Periksa koneksi server atau SAAS_API_BASE_URL.".format(base_url())
        )

    return collected


def voter_identifier(item: Dict[str, Any], source: str) -> Optional[str]:
    """`nisn` untuk siswa, `employee_number` untuk pegawai; fallback external_id/id."""
    if source == "employees":
        candidates: Iterable[Any] = (item.get("employee_number"), item.get("external_id"), item.get("id"))
    else:
        candidates = (item.get("nisn"), item.get("external_id"), item.get("id"))

    for value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _matches_mode(item: Dict[str, Any], source: str, mode: str, employee_type: Optional[str]) -> bool:
    if source == "students":
        if mode != "active":
            return True
        return str(item.get("status") or "").strip().lower() == "active"

    if mode == "by_type":
        if not employee_type:
            raise SaasError("Pilih tipe pegawai (contoh: teacher, staff, general) untuk mode Berdasarkan Type.")
        return str(item.get("type") or "").strip().lower() == employee_type.strip().lower()

    return True


async def sync_voters(
    db,
    source: str,
    mode: str,
    employee_type: Optional[str] = None,
    default_password: Optional[str] = None,
) -> Dict[str, int]:
    """Tarik pemilih dari SaaS lalu simpan yang belum ada di tabel `users`.

    Data dengan `student_id` yang sudah terdaftar dilewati agar tidak duplikat
    dan tidak menimpa suara yang sudah masuk.
    """
    from database import User  # impor lokal menghindari siklus impor

    if source not in ("students", "employees"):
        raise SaasError("Sumber data tidak dikenal. Gunakan 'students' atau 'employees'.")

    if source == "students" and mode not in STUDENT_MODES:
        raise SaasError("Filter sinkronisasi siswa tidak dikenal. Gunakan 'all' atau 'active'.")
    if source == "employees" and mode not in EMPLOYEE_MODES:
        raise SaasError("Filter sinkronisasi pegawai tidak dikenal. Gunakan 'all' atau 'by_type'.")

    params: Dict[str, Any] = {}
    if source == "students" and mode == "active":
        # Dikirim ke API sekaligus disaring ulang di sisi aplikasi bila API mengabaikannya.
        params["status"] = "active"

    items = await fetch_collection(source, params)

    existing_ids = {str(student_id) for (student_id,) in db.query(User.student_id).all()}
    password_fallback = (default_password or "").strip()

    imported = 0
    skipped = 0
    without_id = 0

    for item in items:
        if not _matches_mode(item, source, mode, employee_type):
            continue

        identifier = voter_identifier(item, source)
        if not identifier:
            without_id += 1
            continue

        if identifier in existing_ids:
            skipped += 1
            continue

        full_name = str(item.get("full_name") or "").strip() or None
        db.add(
            User(
                student_id=identifier,
                name=full_name,
                password=password_fallback or identifier,
                role="student",
            )
        )
        existing_ids.add(identifier)
        imported += 1

    if imported:
        try:
            db.commit()
        except Exception as exc:  # noqa: BLE001 - pesan harus ramah untuk admin
            db.rollback()
            raise SaasError("Gagal menyimpan data hasil sinkronisasi: {}".format(exc))

    return {
        "fetched": len(items),
        "imported": imported,
        "skipped": skipped,
        "without_id": without_id,
    }
