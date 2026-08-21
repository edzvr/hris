import urllib.request


URLS = [
    "http://127.0.0.1:5002/",
    "http://127.0.0.1:5002/login",
    "http://127.0.0.1:5002/assessment",
]

for url in URLS:
    try:
        response = urllib.request.urlopen(url, timeout=5)
        print(url, response.getcode(), len(response.read()))
    except Exception as error:
        print(url, "ERROR", repr(error))
