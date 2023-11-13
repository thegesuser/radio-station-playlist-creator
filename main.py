import json
import deezer
import fileinput
import requests
import os
from bs4 import BeautifulSoup, PageElement, ResultSet
from dotenv import load_dotenv
import sqlite3

load_dotenv()

app_Id = os.getenv('APP_ID')
app_secret = os.getenv('APP_SECRET')
redirect_uri = os.getenv('REDIRECT_URI')

con = sqlite3.connect("values.sqlite")
cur = con.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS properties (propname VARCHAR(255) PRIMARY KEY, propval VARCHAR(255));")

def readDlfNovaTracks():
    page = requests.get("https://www.deutschlandfunknova.de/playlist")
    soup = BeautifulSoup(page.content, "html.parser")
    tracks: ResultSet[PageElement] = soup.find_all('figcaption', class_= 'playlist__title')[:10]

    print("done parsing dlf playlist. found {} tracks".format(len(tracks)))

    # As radio stations repeat tracks, we only care for unique tracks
    uniqueTracks = set()
    for singleTrack in tracks:
        title = singleTrack.find('div', class_='title').text
        artist = singleTrack.find('div', class_='artist').text
        uniqueTracks.add((title, artist))
    return uniqueTracks

def getAuthToken():
    token = cur.execute("SELECT propval FROM properties WHERE propname = 'token'").fetchone()
    if token[0] != None:
        return token[0]

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
        cur.execute("INSERT INTO properties VALUES ('token', '{}')".format(new_token))
        con.commit()
        return new_token

token = getAuthToken()
client = deezer.Client(access_token=token) 

# search for deezer track ids using the parsed results
deezerTrackIds = set()
for singleTrack in readDlfNovaTracks():
    result: deezer.PaginatedList[deezer.Track] = client.search(track=singleTrack[0], artist=singleTrack[1], strict=True)
    if len(result) > 0:
        deezerTrackIds.add(result[0].id)

playlist_id = cur.execute("SELECT propval FROM properties WHERE propname = 'playlist_id'").fetchone()
if playlist_id == None:
    playlist_id = client.create_playlist("Deutschlandfunk Nova Playlist")
    cur.execute("INSERT INTO properties VALUES ('playlist_id', '{}')".format(playlist_id))
    con.commit()
else:
    playlist_id = playlist_id[0]

trackIds = ','.join(map(str, deezerTrackIds))
client.request("POST", f"playlist/{playlist_id}/tracks", songs=trackIds)
