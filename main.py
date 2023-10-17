from os.path import exists
import json
import deezer
import fileinput
import requests
import os
from bs4 import BeautifulSoup, PageElement, ResultSet
from dotenv import load_dotenv

load_dotenv()

app_Id = os.getenv('APP_ID')
app_secret = os.getenv('APP_SECRET')
redirect_uri = os.getenv('REDIRECT_URI')

def readCache(name: str):
    if exists(".cache.json"):
        with open(".cache.json", "r") as json_data:
            content = json_data.readline()
            return json.loads(content)[name]
    else: 
        with open(".cache.json", "w+") as json_data:
            json_data.write('{}')

def writeCache(name: str, value: str):
    with open(".cache.json", "r+") as json_data:
        content = json_data.readline()
        data = json.loads(content)
        data[name] = value
        json_data.write(json.dumps(data))

def readDlfNovaTracks():
    page = requests.get("https://www.deutschlandfunknova.de/playlist")
    soup = BeautifulSoup(page.content, "html.parser")
    tracks: ResultSet[PageElement] = soup.find_all('figcaption', class_= 'playlist__title')[:50]

    # As radio stations repeat tracks, we only care for unique tracks
    uniqueTracks = set()
    for singleTrack in tracks:
        title = singleTrack.find('div', class_='title').text
        artist = singleTrack.find('div', class_='artist').text
        uniqueTracks.add((title, artist))
    return uniqueTracks

def getAuthToken():
    token = readCache('token')
    if token != None:
        return token

    print(f"Please open https://connect.deezer.com/oauth/auth.php?app_id={app_Id}&redirect_uri={redirect_uri}&perms=basic_access,email,manage_library,offline_access")
    print("Afterwards, please paste the code into this terminal")
    for line in fileinput.input():
        auth_params = {
            "app_id": app_Id,
            "secret": app_secret,
            "code": line.rstrip(),
            "output": "json"
        }
        authResponse = requests.get('https://connect.deezer.com/oauth/access_token.php', params=auth_params)
        new_token = json.loads(authResponse.content)['access_token']
        writeCache('token', new_token)
        return new_token

token = getAuthToken()
client = deezer.Client(access_token=token) 

# search for deezer track ids using the parsed results
deezerTrackIds = set()
for singleTrack in readDlfNovaTracks():
    result: deezer.PaginatedList[deezer.Track] = client.search(track=singleTrack[0], artist=singleTrack[1], strict=True)
    if len(result) > 0:
        deezerTrackIds.add(result[0].id)


playlist_id = client.create_playlist("Deutschlandfunk Nova Playlist")
trackIds = ','.join(map(str, deezerTrackIds))
client.request("POST", f"playlist/{playlist_id}/tracks", songs=trackIds)
