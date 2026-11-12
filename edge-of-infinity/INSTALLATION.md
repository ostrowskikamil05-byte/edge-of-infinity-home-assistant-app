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

## Uninstall from Edge (0.17.13)

Open **Edge → Cameras / Settings → Setup & data → Uninstall Edge / Odinstaluj
Edge**. The same action is available in the first-run dialog, without completing
setup. The app is always uninstalled; choose the additional data to remove:

- Original recordings, saved alert video clips, thumbnails and snapshots have
  separate choices.
- Playback cache, camera settings/passwords/local HTTPS, Edge setting backups
  and exports, and event/device logs can be selected independently.
- The local HA integration, its entities and component files are optional.
- **Delete all Edge data / Usuń wszystkie dane Edge** selects every category.

Review the delete/keep summary, type **UNINSTALL EDGE**, then confirm. No files
are deleted by previewing. Cleanup runs after an app restart, before camera
connections, recorders, analysis or retention start. Edge then asks Supervisor
to uninstall this app with `remove_config: true`. It does not request elevated
Supervisor privileges or uninstall other apps.

Unchecked recordings and alert videos stay in their original location. Other
unchecked durable Edge files move to
`/homeassistant/edge-retained/<operation-id>/<category>/`; the summary shows the
exact archive path. This includes camera passwords if settings are kept. Retained
settings are not automatically imported by a new installation, and old videos
are not automatically attached. Temporary container/private data is removed by
Supervisor. Only Edge setting backups/exports are covered; full HA system
backup archives and other applications are outside this operation.

Removing configured HA entries requires the updated Edge integration running
in **Home Assistant Core** and an administrator opening Edge from the HA sidebar.
Restart Core once after updating. Edge uses Core's authenticated removal API;
it does not edit `.storage`. Remote Edge entries and their shared component files
remain if another Edge instance uses them. If the bridge is unavailable, the
dialog explains this; other selections work with integration unchecked.

Cleanup failures leave the app in maintenance mode with an explicit retry for
the same selection. Interrupted integration removal can also be resumed before
any media cleanup. `[edge-uninstall]` startup messages report phase/counters and
safe error codes. A disconnected page alone does not confirm uninstall: the
dialog checks HA when possible and otherwise asks you to check its app list.
Do not assume a failed operation rolled back files already removed. Cancellation
is available before file cleanup is queued.

## Reset data while keeping the app

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

Use **Uninstall Edge** above when removing those shared files and the app together.
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
