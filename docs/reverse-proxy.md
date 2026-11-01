# Reverse proxy

Behind a reverse proxy, set `PROXY_COUNT=1`. The label designer can then also be served below a path, e.g. with nginx:

```nginx
location /labels/ {
    proxy_pass http://127.0.0.1:8013/;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Prefix /labels;
}
```
