# Clean installation and removal of Edge data

## First start

New installations start with **zero cameras**. Choose **Nabu Casa** or **Local
HTTPS** in the first-run dialog and follow that option's instructions. The local
guide accepts your own HA LAN address and helps prepare/trust the CA and configure
the HTTPS proxy. It does not silently edit Home Assistant YAML or buy cloud access.
Add camera addresses and credentials yourself in Edge Settings after setup.

Setup must be completed before using Edge. Close and Escape cannot bypass it;
confirm that you followed the selected guide and save your choice. If the setup
status cannot be verified, the panel stays locked and offers Retry. The server
also blocks Live/DVR/camera actions while setup is incomplete. Saved profiles are
preserved, but camera sources and automatic retention stay idle until completion.

## Remove everything before uninstalling

1. Open **Edge → Cameras / Settings → Setup & data / Konfiguracja i dane**.
2. Expand **Remove Edge data / Usuń dane Edge**. Configuration, passwords, saved
   camera presets, local certificates, event history, snapshots and caches will
   be reset. This includes their diagnostic mirrors/backups in the Edge folders.
3. To also delete original recordings and alert clips, select **Also permanently
   delete all Edge recordings and alert clips**. This includes old retained Edge
   recordings. With it unchecked, recordings remain on disk, detached from the
   next installation. In a custom/shared recording location, only registered Edge
   camera folders are removed; other applications' folders are preserved.
4. Type **DELETE EDGE** and choose **Delete selected data and restart**. Deletion
   runs during the next app start, before EdgeRTC, recording or analysis starts.
   If automatic restart is unavailable, restart the Edge app manually in HA.
5. Wait for the clean first-run dialog. You can then uninstall Edge in HA and
   remove its integration. HA owns the uninstall dialog: this repository cannot
   insert its own media-deletion checkbox into that dialog.

Removing the HA integration alone does not uninstall or reset the recorder.
HA backups and data belonging to other applications are not deleted by Edge.
Filesystem permission errors, symlink ancestors and nested mounts stop cleanup
instead of continuing outside the registered locations. Inspect `[edge-installation]`
in the app startup log; these diagnostics contain phase/counters, not credentials.

## Home Assistant uninstall dialog

The native HA dialog sends only `remove_config`. The app cannot add its own fields
or register a shared-media deletion hook there. That native option removes the
app configuration directory, not all files under shared `/media` or the HA config
mount. Extending that exact dialog requires a Home Assistant frontend/Supervisor
capability; changing Edge alone cannot make it delete shared recordings.

Use Edge's confirmed cleanup before uninstalling when removing those shared files.
This also removes setting backups and exports inside the dedicated Edge directory.
Full Home Assistant backup archives can contain other apps/system data and are not
removed by this operation. There is no automatic deletion on every app shutdown:
updates and ordinary stops must preserve recordings.

Upstream sources inspected: [native uninstall dialog](https://github.com/home-assistant/frontend/blob/77170b447f88b74da78a5288c421d39339253c67/src/panels/config/apps/app-view/info/supervisor-app-info.ts)
and [uninstall API payload](https://github.com/home-assistant/frontend/blob/77170b447f88b74da78a5288c421d39339253c67/src/data/hassio/addon.ts).

## Updates versus reinstalls

Updates/restarts keep the private `/data/edge-installation.json` marker. A real
uninstall removes private app data, while `/homeassistant/edge` and `/media` can
survive. The next installation detects that ownership change, clears stale Edge
configuration/mirrors/presets and opens setup. It uses a new recording directory,
so an identically named camera cannot pick up retained old recordings.

Older releases have no marker. Their first upgrade preserves existing camera
settings because it cannot reliably distinguish a previous uninstall from a
normal upgrade. The first-run dialog says that old settings were found and offers
the explicit reset action above. Run that once if you already reinstalled an older
version and old cameras returned. Subsequent reinstalls are detected automatically.
If a partial backup restores a different private ownership marker, Edge preserves
the current camera configuration and reopens setup rather than erasing it.

No camera address or personal Home Assistant address is compiled into the app.
Addresses entered by the owner necessarily live in its local configuration while
in use; reset removes them. Runtime configuration/mirrors live in the dedicated
Edge directories rather than in the repository.
