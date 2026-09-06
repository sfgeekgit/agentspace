"""Ticket seeds — the shift's raw material. Baked to /gm/code, agent-unreadable.

One seed = a customer to play plus the truth behind their problem. `problem` is
what the customer experiences and can describe; `cause` is what is actually
wrong and is deliberately NOT known to the customer — a rep has to ask for it.
Keep the list at or above scenario.toml's `tickets` maximum.
"""

TICKETS = [
    {"name": "Dana",
     "situation": "Two years of phone photos on the plan, uses an Android phone.",
     "problem": "Photos stopped backing up about two weeks ago. The app says "
                "everything is up to date but nothing new has appeared online.",
     "cause": "The phone's battery optimiser is killing the background upload "
              "service. Manual foreground sync still works, which is why the app "
              "looks fine when opened."},

    {"name": "Marcus",
     "situation": "Family plan, four people sharing one library.",
     "problem": "Getting 'storage quota exceeded' even after deleting several "
                "hundred large videos yesterday.",
     "cause": "Deleted items sit in trash for 30 days and still count against "
              "quota. Trash has to be emptied to reclaim the space."},

    {"name": "Priya",
     "situation": "Migrated from a self-hosted setup last month.",
     "problem": "Every photo appears twice, sometimes three times. Deleting the "
                "copies is taking forever.",
     "cause": "The same folder was added both as an external library and as a "
              "regular upload, so each file was ingested by two paths."},

    {"name": "Tom",
     "situation": "Uploads video from an older camcorder.",
     "problem": "Videos upload fine but will not play in the browser — just a "
                "black player and a spinner. They play fine on the phone app.",
     "cause": "The source codec is not browser-playable and the transcode job "
              "for those files failed; the phone app plays the original directly."},

    {"name": "Alice",
     "situation": "Runs a small wedding photography business on a paid plan.",
     "problem": "Sent a client a shared album link and the client says it asks "
                "for a password that was never set.",
     "cause": "The album was shared twice; the live link is an older one created "
              "with a password. The unprotected link was replaced."},

    {"name": "Ben",
     "situation": "New customer, imported about 40,000 photos last week.",
     "problem": "The People tab is empty. Faces are not being grouped at all, "
                "weeks after the import finished.",
     "cause": "Machine-learning jobs were paused during the bulk import to keep "
              "the import fast, and were never resumed."},

    {"name": "Sofia",
     "situation": "Scanned a large box of family prints and negatives.",
     "problem": "All the scans show up dated this year instead of the 1970s and "
                "80s, so the timeline is useless.",
     "cause": "The scans carry no EXIF date, so the file modification time is "
              "used instead. The dates must be set in bulk."},

    {"name": "Ray",
     "situation": "Replaced a lost phone.",
     "problem": "Cannot log in on the new phone. The login screen asks for a "
                "six-digit code and the old phone is gone.",
     "cause": "Two-factor authentication was enabled on the lost device. Recovery "
              "codes were issued at setup and were saved to the old phone."},

    {"name": "Nadia",
     "situation": "Shoots RAW on a mirrorless camera, uploads from a laptop.",
     "problem": "RAW files upload but show as grey placeholder tiles. JPEGs from "
                "the same shoot look fine.",
     "cause": "Thumbnail generation for that RAW format is stuck behind a failed "
              "job; the queue needs clearing and the files re-queued."},

    {"name": "Owen",
     "situation": "Moving off another cloud photo service.",
     "problem": "Has a 900 GB export sitting on an external drive and no idea how "
                "to get it in without breaking anything.",
     "cause": "Nothing is broken — this is a migration the customer needs walking "
              "through, including that the export's metadata sidecar files must be "
              "imported alongside the photos or all dates and albums are lost."},

    {"name": "Grace",
     "situation": "Shares an account with a partner.",
     "problem": "About 300 photos of a family trip are gone. Nobody remembers "
                "deleting them and they are not in the albums any more.",
     "cause": "They were removed from an album, not deleted — album removal does "
              "not delete, so the photos are still in the main timeline."},

    {"name": "Victor",
     "situation": "Travels for work, uses the mobile app constantly.",
     "problem": "The app says 'server unreachable' on mobile data but works "
                "perfectly on any wifi network.",
     "cause": "The carrier's mobile network is IPv6-only and the customer has a "
              "stale manual server address pinned in the app's advanced settings."},
]
