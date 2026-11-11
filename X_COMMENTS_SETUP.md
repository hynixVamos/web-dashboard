# Free X comments

The US new-highs page reads `/static/x-comments-YYYY-MM-DD.json` and displays
attributed public-post excerpts. No paid X API calls are used. Each snapshot is
bound to the selected report date. Missing data stays visibly unavailable.

The initial snapshot was collected from the owner's logged-in X browser. It is
a dated snapshot, not a claim of live or unattended collection.

## Optional unattended collection

`x_comments.py` uses Twikit 2.3.3 public search through an X web session. Configure
`X_SESSION_COOKIES_JSON` in Render's private environment variables as a JSON
object containing `auth_token` and `ct0` from an account you control. Never put
session cookies in this repository, screenshots, public forms, or chat messages.
The Bearer Token created for the official API is not used by this collector.

When configured, the existing post-fork background startup launches a separate
hourly worker. It collects up to five excerpts for each of the top 20 rising
new-high stocks once per report date, pauses five seconds between searches, and
stops on provider/login/rate errors. Failures can retry after one hour. It never
posts, likes, follows, or uses the paid API. It does not change price collection.

X can expire the session or change its web endpoints; collection is best-effort.
If the session expires, existing dated snapshots remain displayed and search
links remain usable. No login challenges or CAPTCHA are bypassed.

Render's writable filesystem is temporary unless a persistent disk is configured.
Dynamic snapshots must be backed up if they should survive a redeploy. Committed
initial snapshots survive redeploys. A paid Render disk is not required just to
display the committed initial snapshot.

Local check: `python -m unittest discover -s tests -p test_x_comments.py`.
Manual collector run with the same private environment: `python x_comments.py`.
