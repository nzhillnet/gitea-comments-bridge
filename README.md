# gitea-comments-bridge

A self-hosted comment system for static sites, backed by a private Gitea instance.

No separate database, no third-party SaaS, no tracking. Each page's slug maps to a Gitea
issue, and that issue's comments are the comment store — Gitea becomes the database. A
small Flask service holds the Gitea API token server-side, so the browser never sees a
credential; your site only ever talks to this bridge over two endpoints:

```
GET  /comments/<slug>   -> fetch comments for a page
POST /comments/<slug>   -> add a comment
```

Full write-up, with the reasoning behind the design and the architecture diagram:
**https://hillnet.co.nz/projects/self-hosted-hugo-comments-private-gitea/**

## How it works

```
Browser --/comments/*--> your reverse proxy --> this bridge --> Gitea issues API
```

- Gitea stays on a private network. Nothing public ever talks to it directly — only
  this bridge does, using a scoped API token.
- Your site's static build never changes when comments arrive; they're fetched
  client-side at page load.
- Moderation is just editing or deleting the underlying Gitea issue comment.

## Setup

1. Create (or reuse) a Gitea repository to hold the comments-as-issues.
2. Generate a Gitea access token scoped to **issue read + write** on that repo only.
3. Copy `docker-compose.example.yml` to `docker-compose.yml`, fill in the required
   environment variables, and run:

   ```bash
   docker compose up -d --build
   ```
4. Point your reverse proxy's `/comments/` path at the bridge's port. An nginx example:

   ```nginx
   location ^~ /comments/ {
       proxy_pass http://127.0.0.1:18080;   # wherever the container's host:port is

       proxy_http_version 1.1;
       proxy_set_header Connection "";
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_buffering off;
   }
   ```
5. Wire up your site's frontend. A minimal vanilla-JS example (this is what the Hugo
   shortcode behind the write-up above does):

   ```javascript
   const slug = "{{ .Params.slug }}";   // however your site templating gives you the slug

   fetch(`/comments/${slug}`)
     .then(r => r.json())
     .then(comments => { /* render comments */ });

   function postComment(author, body) {
     return fetch(`/comments/${slug}`, {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify({ body: `**${author}**:\n\n${body}` }),
     });
   }
   ```

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GITEA_URL` | yes | — | Your Gitea instance's API base, e.g. `https://gitea.example.com/api/v1` |
| `REPO_OWNER` | yes | — | Owner of the repo comments live in |
| `REPO_NAME` | yes | — | That repo's name |
| `GITEA_TOKEN` | yes | — | Scoped token (issue read + write) |
| `SITE_NAME` | no | `REPO_NAME` | Used in e-mail alert subject lines |
| `SITE_URL` | no | — | Used to build a fallback post link in e-mail alerts |
| `MAIL_RELAY_HOST` | no | — | SMTP relay for new-comment notifications. Unset = notifications disabled |
| `MAIL_RELAY_PORT` | no | `25` | SMTP port |
| `MAIL_FROM` | no | `comments@<SITE_NAME>` | Notification sender |
| `MAIL_TO` | no | — | Notification recipient (required if `MAIL_RELAY_HOST` is set) |

## Notes

- This is a small, single-file tool built for one personal site — read `bridge.py`
  before trusting it with anything sensitive. The moderation function is a trivial
  banned-word filter; extend it to suit.
- Run it behind a reverse proxy, not exposed directly (the bundled Flask server is
  development-grade).
- Comments post as plain Markdown, so basic formatting works, but there's no
  anti-spam/rate-limiting beyond the moderation hook — add what your site needs.
