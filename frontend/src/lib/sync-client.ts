// Backup-ZIP client only. No sync server, no cloud: export downloads a
// ZIP blob, import uploads one. Server owns merge/LWW.
const BASE = "/api/v1";

export async function downloadBackupZip(token: string): Promise<Blob> {
  const res = await fetch(`${BASE}/admin/backup`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`backup export failed: ${res.status}`);
  return await res.blob();
}

// Import = upload a previously exported ZIP; server merges in staging.
export async function uploadBackupZip(
  token: string,
  file: Blob,
): Promise<void> {
  const fd = new FormData();
  fd.append("backup", file, "inkcast-backup.zip");
  const res = await fetch(`${BASE}/admin/backup`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: fd,
  });
  if (!res.ok) throw new Error(`backup import failed: ${res.status}`);
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
