"""A minimal GitHub REST and GraphQL client on the standard library."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class GitHubError(Exception):
    def __init__(self, status, message):
        super().__init__(f'GitHub answered {status}: {message}')
        self.status = status


class GitHub:
    def __init__(self, token, api=None):
        """`token` is a string, or a callable returning the current one (an App installation token)."""
        self.token = token
        self.api = (api or os.environ.get('GITHUB_API_URL') or 'https://api.github.com').rstrip('/')

    def request(self, method, path, body=None, params=None, raw=False):
        url = f'{self.api}/{path.lstrip("/")}' + (f'?{urllib.parse.urlencode(params)}' if params else '')
        token = self.token() if callable(self.token) else self.token
        headers = {'Authorization': f'Bearer {token}', 'X-GitHub-Api-Version': '2022-11-28',
                   'Accept': 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json',
                   'User-Agent': 'superagent-uat'}
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            raise GitHubError(error.code, error.read().decode('utf-8', 'replace')[:500]) from None
        if raw:
            return payload
        return json.loads(payload) if payload else None

    def get(self, path, **params):
        return self.request('GET', path, params=params or None)

    def paginate(self, path, key=None, limit=3000, **params):
        """Every item of a paginated list (or of its `key` field), up to `limit`."""
        items, page = [], 1
        while len(items) < limit:
            batch = self.get(path, per_page=100, page=page, **params)
            batch = batch[key] if key else batch
            items += batch
            if len(batch) < 100:
                break
            page += 1
        return items[:limit]

    def graphql(self, query, **variables):
        answer = self.request('POST', 'graphql', {'query': query, 'variables': variables})
        if answer.get('errors'):
            raise GitHubError(200, json.dumps(answer['errors'])[:500])
        return answer['data']
