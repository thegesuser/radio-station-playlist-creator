import json
import os
import sqlite3
import time

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
    cur.execute("INSERT INTO properties VALUES ('{}', '{}')".format(prop_name, value))
    con.commit()


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
    if persisted_token is None:
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
    else:
        print("read token from storage")
        return json.loads(persisted_token[0])


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


def update_playlist(playlist_name: str, prop_name: str, track_ids: set):
    playlist_id = get_single_prop(prop_name)
    if playlist_id is None:
        playlist_id = token.create_playlist(playlist_name)
        cur.execute("INSERT INTO properties VALUES ('{}', '{}')".format(prop_name, playlist_id))
        con.commit()
    else:
        playlist_id = playlist_id[0]
        track_ids_to_delete = get_track_ids_in_playlist(playlist_id)
        delete_tracks_from_playlist(playlist_id, track_ids_to_delete)
    token.request("POST", f"playlist/{playlist_id}/tracks", songs=comma_separated_list(track_ids))


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


dlf_nova_tracks = list(read_dlf_nova_tracks())
auth = OAuth2Auth(get_tidal_token())
tidal_tracks = find_tidal_track_ids(dlf_nova_tracks, auth)

config = tidalapi.Config()
session = tidalapi.Session(config)
tidal_token = get_unofficial_tidal_client(session)
playlist_id = get_single_prop('tidal_playlist_id')
if playlist_id is None:
    playlist = session.user.create_playlist("Deutschlandfunk Nova Playlist",
                                            "Nur ❤️ für den Deutschlandfunk und deren Musikkuratoren. Diese Playlist wird automatisch und regelmäßig mit der aktuellen Playlist von Deutschlandfunk Nova abgeglichen.")
    persist_value('tidal_playlist_id', playlist.id)
else:
    playlist = session.playlist(playlist_id[0])

no_of_tracks_in_playlist = len(playlist.tracks())
print("Purging existing {} tracks before adding new ones".format(no_of_tracks_in_playlist))
if no_of_tracks_in_playlist > 0:
    playlist.remove_by_indices([idx for idx in range(no_of_tracks_in_playlist)])
time.sleep(10)

# add tracks to list in a chunked fashion
playlist = session.playlist(playlist_id)
for idx, chunk in enumerate(chunks(list(tidal_tracks), 10)):
    playlist.add(chunk)
    print("Successfully added chunk {}".format(idx))
