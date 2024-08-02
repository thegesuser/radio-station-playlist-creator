import json
import os
import re
import sqlite3
import time
from typing import List

import deezer
import numpy
import requests
import tidalapi
from authlib.integrations.requests_client import OAuth2Auth, OAuth2Session
from bs4 import BeautifulSoup, PageElement, ResultSet
from dotenv import load_dotenv

load_dotenv()

app_Id = os.getenv('APP_ID')
app_secret = os.getenv('APP_SECRET')
tidal_app_Id = os.getenv('TIDAL_APP_ID')
tidal_app_secret = os.getenv('TIDAL_APP_SECRET')
redirect_uri = os.getenv('REDIRECT_URI')

con = sqlite3.connect("values.sqlite")
cur = con.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS properties (prop_name VARCHAR(255) PRIMARY KEY, prop_val VARCHAR(255));")
cur.execute("CREATE TABLE IF NOT EXISTS song_cache (query_name VARCHAR(255) PRIMARY KEY, track_id VARCHAR(255));")


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


def get_cached_track(query):
    track_id = cur.execute("SELECT track_id FROM song_cache WHERE query_name = ?;", [query]).fetchone()
    return track_id


def cache_track_query(search_query: str, track_id: str):
    cur.execute("INSERT INTO song_cache VALUES (?, ?)", (search_query, track_id))
    con.commit()


def get_single_prop(prop_name):
    playlist_id = cur.execute("SELECT prop_val FROM properties WHERE prop_name = '{}'".format(prop_name)).fetchone()
    return playlist_id


def persist_value(prop_name: str, value: str):
    cur.execute("DELETE FROM properties WHERE prop_name = ?", [prop_name])
    cur.execute("INSERT INTO properties VALUES ('{}', '{}')".format(prop_name, value))
    con.commit()


def get_deezer_auth_token():
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


def get_unofficial_tidal_client(p_session: tidalapi.Session):
    tidal_access_token = get_single_prop('tidal_access_token')
    if tidal_access_token is not None:
        tidal_expiry_time = get_single_prop('tidal_expiry_time')[0]
        tidal_token_type = get_single_prop('tidal_token_type')[0]
        tidal_refresh_token = get_single_prop('tidal_refresh_token')[0]
        p_session.load_oauth_session(tidal_token_type, tidal_access_token[0], tidal_refresh_token, tidal_expiry_time)

    if not p_session.check_login():
        print("need to get new token for unofficial client")
        p_session.login_oauth_simple()
        cur.execute("INSERT INTO properties VALUES ('tidal_access_token', '{}')".format(p_session.access_token))
        cur.execute("INSERT INTO properties VALUES ('tidal_expiry_time', '{}')".format(p_session.expiry_time))
        cur.execute("INSERT INTO properties VALUES ('tidal_token_type', '{}')".format(p_session.token_type))
        cur.execute("INSERT INTO properties VALUES ('tidal_refresh_token', '{}')".format(p_session.refresh_token))
        con.commit()

    return session.access_token


def get_tidal_token():
    persisted_token = get_single_prop('tidal_token')
    if persisted_token is not None:
        print("read token from storage")
        token_object = json.loads(persisted_token[0])
        if (token_object['expires_at']) > int(time.time()):
            # only return token if it's valid, otherwise fetch a new one
            return token_object

    print("getting new token")
    token_endpoint = 'https://auth.tidal.com/v1/oauth2/token'
    my_session = OAuth2Session(
        tidal_app_Id,
        tidal_app_secret,
        token_endpoint_auth_method='client_secret_post'
    )
    token_value = my_session.fetch_token(token_endpoint)
    persist_value('tidal_token', json.dumps(token_value))
    return token_value


def find_deezer_track_ids(my_client: deezer.Client, parsed_tracks: set):
    return_value = set()
    for single_track in parsed_tracks:
        quasi_sanitized_track = re.sub('[!?&]', '', single_track[0])
        quasi_sanitized_artist = re.sub('[!?&]', '', single_track[1])
        deezer_search_result: deezer.PaginatedList[deezer.Track] = my_client.search(track=quasi_sanitized_track,
                                                                                    artist=quasi_sanitized_artist,
                                                                                    strict=True)
        if len(deezer_search_result) > 0:
            return_value.add(deezer_search_result[0].id)
    return return_value


def get_track_ids_in_playlist(my_client: deezer.Client, playlist_id: str):
    tracks = []
    index = 0
    while True:
        playlist = my_client.request("GET", f"playlist/{playlist_id}/tracks?index={index}", paginate_list=True)
        for track in playlist['data']:
            tracks.append(track.id)
        if 'next' in playlist.keys():
            index += 25
        else:
            break
    return tracks


def delete_tracks_from_playlist(my_client: deezer.Client, playlist_id: str, track_ids: List[str]):
    if len(track_ids) > 0:
        for chunk in numpy.array_split(numpy.array(track_ids), 10):
            my_client.request("DELETE", f"playlist/{playlist_id}/tracks", songs=comma_separated_list(chunk))


def find_tidal_track_ids(parsed_tracks: list, auth: OAuth2Auth):
    return_value = set()
    total_tracks = len(parsed_tracks)
    for track_idx, single_track in enumerate(parsed_tracks):
        query_string = ' '.join(single_track).replace(" feat. ", " ")

        cached_query = get_cached_track(query_string)
        if cached_query is not None:
            return_value.add(cached_query[0])
            print("({}/{}) found track for query '{}' in cache".format(track_idx, total_tracks, query_string))
        else:
            song_query = requests.get("https://openapi.tidal.com/v2/searchresults/" + query_string,
                                      auth=auth,
                                      params={'include': "tracks",
                                              'countryCode': 'DE'},
                                      headers={
                                          'Content-Type': 'application/vnd.api+json',
                                          'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36'
                                      })

            try:
                json_value = json.loads(song_query.text)
                if json_value is not None:
                    found_tracks = json_value['data']['relationships']['tracks']['data']
                    if len(found_tracks) > 0:
                        track_id = found_tracks[0]['id']
                        return_value.add(track_id)
                        print("({}/{}) found track with id {} for query '{}'".format(track_idx, total_tracks, track_id,
                                                                                     query_string))
                        cache_track_query(query_string, track_id)
                    else:
                        print(
                            "({}/{}) could not find track for query '{}'".format(track_idx, total_tracks, query_string))
            except:
                print("({}/{}) [{}]: {}".format(track_idx, total_tracks, song_query.status_code, song_query.text))
            time.sleep(5)

    return return_value


def update_deezer_playlist(my_client: deezer.Client, playlist_name: str, prop_name: str, track_ids: set):
    playlist_id = get_single_prop(prop_name)
    if playlist_id is None:
        playlist_id = token.create_playlist(playlist_name)
        playlist_object: deezer.Playlist = client.get_playlist(playlist_id)
        cur.execute("INSERT INTO properties VALUES ('{}', '{}')".format(prop_name, playlist_id))
        con.commit()
    else:
        playlist_id = playlist_id[0]
        track_ids_to_delete = get_track_ids_in_playlist(my_client, playlist_id)
        playlist_object: deezer.Playlist = client.get_playlist(playlist_id)
        playlist_object.delete_tracks(track_ids_to_delete)
    playlist_object.add_tracks(track_ids)


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def update_tidal_playlist(p_session: tidalapi.Session):
    playlist_id = get_single_prop('tidal_playlist_id')
    if playlist_id is None:
        playlist = p_session.user.create_playlist("Deutschlandfunk Nova Playlist",
                                                  "Nur ❤️ für den Deutschlandfunk und deren Musikkuratoren. Diese Playlist wird automatisch und regelmäßig mit der aktuellen Playlist von Deutschlandfunk Nova abgeglichen.")
        persist_value('tidal_playlist_id', playlist.id)
    else:
        playlist = p_session.playlist(playlist_id[0])

    no_of_tracks_in_playlist = len(playlist.tracks())
    print("Purging existing {} tracks before adding new ones".format(no_of_tracks_in_playlist))
    if no_of_tracks_in_playlist > 0:
        playlist.remove_by_indices([idx for idx in range(no_of_tracks_in_playlist)])
    time.sleep(10)

    # add tracks to list in a chunked fashion
    playlist = p_session.playlist(playlist_id)
    for idx, chunk in enumerate(chunks(list(tidal_tracks), 10)):
        playlist.add(chunk)
        print("Successfully added chunk {}".format(idx))


# ========= COMMON =========
dlf_nova_tracks = read_dlf_nova_tracks()

# ========= DEEZER =========
token = get_deezer_auth_token()
client = deezer.Client(access_token=token)

# search for deezer track ids using the parsed results and update
dlf_nova_track_ids = find_deezer_track_ids(client, dlf_nova_tracks)
update_deezer_playlist(client, "Deutschlandfunk Nova Playlist", 'dlf_nova_playlist_id', dlf_nova_track_ids)

einslive_track_ids = find_deezer_track_ids(client, read_einslive_plan_b_tracks())
update_deezer_playlist(client, "1LIVE Plan B Playlist", 'einslive_plan_b_playlist_id', einslive_track_ids)

# ========= TIDAL =========
auth = OAuth2Auth(get_tidal_token())
tidal_tracks = find_tidal_track_ids(list(dlf_nova_tracks), auth)

config = tidalapi.Config()
session = tidalapi.Session(config)
tidal_token = get_unofficial_tidal_client(session)
update_tidal_playlist(session)
