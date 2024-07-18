import fileinput
import json
import os
import re
import sqlite3
from typing import List

import deezer
import numpy
import requests
from bs4 import BeautifulSoup, PageElement, ResultSet
from dotenv import load_dotenv

load_dotenv()

app_Id = os.getenv('APP_ID')
app_secret = os.getenv('APP_SECRET')
redirect_uri = os.getenv('REDIRECT_URI')

con = sqlite3.connect("values.sqlite")
cur = con.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS properties (prop_name VARCHAR(255) PRIMARY KEY, prop_val VARCHAR(255));")


def read_dlf_nova_tracks():
    page = requests.get("https://www.deutschlandfunknova.de/playlist")
    soup = BeautifulSoup(page.content, "html.parser")
    tracks: ResultSet[PageElement] = soup.find_all('figcaption', class_='playlist__title')[:150]

    print("done parsing dlf playlist. found {} tracks".format(len(tracks)))

    # As radio stations repeat tracks, we only care for unique tracks
    unique_tracks = set()
    for single_track in tracks:
        title = single_track.find('div', class_='title').text
        artist = single_track.find('div', class_='artist').text
        unique_tracks.add((title, artist))
    return unique_tracks


def read_einslive_plan_b_tracks():
    pages = [
        'https://www1.wdr.de/radio/1live/musik/1live-plan-b/plan-b-montagssendung-100.html',
        'https://www1.wdr.de/radio/1live/musik/1live-plan-b/plan-b-dienstagssendung-100.html',
        'https://www1.wdr.de/radio/1live/musik/1live-plan-b/plan-b-mittwochssendung-100.html',
        'https://www1.wdr.de/radio/1live/musik/1live-plan-b/plan-b-donnerstagssendung-100.html'
    ]

    # As radio stations repeat tracks, we only care for unique tracks
    unique_tracks = set()
    for show in pages:
        page = requests.get(show)
        soup = BeautifulSoup(page.content, "html.parser")
        tracks: ResultSet[PageElement] = soup.find_all('tr', class_='data')
        print("done parsing einslive playlist. found {} rows".format(len(tracks)))

        for single_track in tracks:
            single_row = single_track.find_all('td', class_='entry')
            artist = single_row[0].text
            title = single_row[1].text
            if (artist == 'Interpret' or title == 'Titel') or (artist == '' or title == ''):
                # filter out headers and dividers
                continue
            unique_tracks.add((title, artist))
    return unique_tracks


def comma_separated_list(p_list):
    return ",".join(map(str, p_list))


def get_auth_token():
    db_token = cur.execute("SELECT prop_val FROM properties WHERE prop_name = 'token'").fetchone()
    if db_token is not None:
        return db_token[0]

    print(
        f"Please open https://connect.deezer.com/oauth/auth.php?app_id={app_Id}&redirect_uri={redirect_uri}&perms=basic_access,email,manage_library,delete_library,offline_access")
    print("Afterwards, please paste the code into this terminal")
    for line in fileinput.input():
        auth_params = {
            "app_id": app_Id,
            "secret": app_secret,
            "code": line.rstrip(),
            "output": "json",
        }
        auth_response = requests.get("https://connect.deezer.com/oauth/access_token.php", params=auth_params)
        new_token = json.loads(auth_response.content)["access_token"]
        cur.execute("INSERT INTO properties VALUES ('token', '{}')".format(new_token))
        con.commit()
        return new_token


def get_track_ids_in_playlist(playlist_id: str):
    tracks = []
    index = 0
    while True:
        playlist = client.request("GET", f"playlist/{playlist_id}/tracks?index={index}", paginate_list=True)
        for track in playlist['data']:
            tracks.append(track.id)
        if 'next' in playlist.keys():
            index += 25
        else:
            break
    return tracks


def delete_tracks_from_playlist(playlist_id: str, track_ids: List[str]):
    if len(track_ids) > 0:
        for chunk in numpy.array_split(numpy.array(track_ids), 10):
            client.request("DELETE", f"playlist/{playlist_id}/tracks", songs=comma_separated_list(chunk))


def find_deezer_track_ids(parsed_tracks: set):
    return_value = set()
    for single_track in parsed_tracks:
        quasi_sanitized_track = re.sub('[!?&]', '', single_track[0])
        quasi_sanitized_artist = re.sub('[!?&]', '', single_track[1])
        deezer_search_result: deezer.PaginatedList[deezer.Track] = client.search(track=quasi_sanitized_track,
                                                                                 artist=quasi_sanitized_artist,
                                                                                 strict=True)
        if len(deezer_search_result) > 0:
            return_value.add(deezer_search_result[0].id)
    return return_value


def update_playlist(playlist_name: str, prop_name: str, track_ids: set):
    playlist_id = cur.execute("SELECT prop_val FROM properties WHERE prop_name = '{}'".format(prop_name)).fetchone()
    if playlist_id is None:
        playlist_id = client.create_playlist(playlist_name)
        playlist_object: deezer.Playlist = client.get_playlist(playlist_id)
        cur.execute("INSERT INTO properties VALUES ('{}', '{}')".format(prop_name, playlist_id))
        con.commit()
    else:
        playlist_id = playlist_id[0]
        track_ids_to_delete = get_track_ids_in_playlist(playlist_id)
        playlist_object: deezer.Playlist = client.get_playlist(playlist_id)
        playlist_object.delete_tracks(track_ids_to_delete)

    playlist_object.add_tracks(track_ids)


token = get_auth_token()
client = deezer.Client(access_token=token)

# search for deezer track ids using the parsed results and update
dlf_nova_track_ids = find_deezer_track_ids(read_dlf_nova_tracks())
update_playlist("Deutschlandfunk Nova Playlist", 'dlf_nova_playlist_id', dlf_nova_track_ids)

einslive_track_ids = find_deezer_track_ids(read_einslive_plan_b_tracks())
update_playlist("1LIVE Plan B Playlist", 'einslive_plan_b_playlist_id', einslive_track_ids)
