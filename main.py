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
from deezer import Client
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


class RadioStationPlaylistPage:

    def get_tracks(self):
        pass


class DlfNova(RadioStationPlaylistPage):

    def __init__(self):
        self.url = "https://www.deutschlandfunknova.de/playlist"

    def get_tracks(self):
        page = requests.get(self.url)
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


class EinslivePlanB(RadioStationPlaylistPage):

    def __init__(self):
        self.urls = [
            'https://www1.wdr.de/radio/1live/musik/1live-plan-b/plan-b-montagssendung-100.html',
            'https://www1.wdr.de/radio/1live/musik/1live-plan-b/plan-b-dienstagssendung-100.html',
            'https://www1.wdr.de/radio/1live/musik/1live-plan-b/plan-b-mittwochssendung-100.html',
            'https://www1.wdr.de/radio/1live/musik/1live-plan-b/plan-b-donnerstagssendung-100.html'
        ]

    def get_tracks(self):
        # As radio stations repeat tracks, we only care for unique tracks
        unique_tracks = set()
        for show in self.urls:
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


class MusicServiceWorker:
    def update_playlists(self, playlist_name: str, playlist_id_row_id: str, tracks: set):
        pass


class DeezerWorker(MusicServiceWorker):
    def __init__(self):
        self.token = self.get_deezer_auth_token()
        self.client: Client = deezer.Client(access_token=self.token)

    def get_deezer_auth_token(self):
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

    def update_playlists(self, playlist_name: str, playlist_id_row_id: str, tracks: set):
        # search for deezer track ids using the parsed results and update
        track_ids = self.find_deezer_track_ids(tracks)
        self.update_playlist_internal(playlist_name, playlist_id_row_id, track_ids)

    def update_playlist_internal(self, playlist_name: str, prop_name: str, track_ids: set):
        playlist_id = get_single_prop(prop_name)
        if playlist_id is None:
            playlist_id = self.client.create_playlist(playlist_name)
            playlist_object: deezer.Playlist = self.client.get_playlist(playlist_id)
            cur.execute("INSERT INTO properties VALUES ('{}', '{}')".format(prop_name, playlist_id))
            con.commit()
        else:
            playlist_id = playlist_id[0]
            track_ids_to_delete = self.get_track_ids_in_playlist(playlist_id)
            playlist_object: deezer.Playlist = self.client.get_playlist(playlist_id)
            playlist_object.delete_tracks(track_ids_to_delete)
        playlist_object.add_tracks(track_ids)

    def find_deezer_track_ids(self, parsed_tracks: set):
        return_value = set()
        for single_track in parsed_tracks:
            quasi_sanitized_track = re.sub('[!?&]', '', single_track[0])
            quasi_sanitized_artist = re.sub('[!?&]', '', single_track[1])
            deezer_search_result: deezer.PaginatedList[deezer.Track] = self.client.search(track=quasi_sanitized_track,
                                                                                          artist=quasi_sanitized_artist,
                                                                                          strict=True)
            if len(deezer_search_result) > 0:
                return_value.add(deezer_search_result[0].id)
        return return_value

    def get_track_ids_in_playlist(self, playlist_id: str):
        tracks = []
        index = 0
        while True:
            client = self.client
            playlist = client.request("GET", f"playlist/{playlist_id}/tracks?index={index}", paginate_list=True)
            for track in playlist['data']:
                tracks.append(track.id)
            if 'next' in playlist.keys():
                index += 25
            else:
                break
        return tracks


class TidalWorker(MusicServiceWorker):
    def __init__(self):
        self.auth = OAuth2Auth(self.get_tidal_token())
        config = tidalapi.Config()
        self.session = tidalapi.Session(config)
        self.tidal_token = self.get_unofficial_tidal_client()

    def get_unofficial_tidal_client(self):
        tidal_access_token = get_single_prop('tidal_access_token')
        if tidal_access_token is not None:
            tidal_expiry_time = get_single_prop('tidal_expiry_time')[0]
            tidal_token_type = get_single_prop('tidal_token_type')[0]
            tidal_refresh_token = get_single_prop('tidal_refresh_token')[0]
            self.session.load_oauth_session(tidal_token_type, tidal_access_token[0], tidal_refresh_token,
                                            tidal_expiry_time)

        if not self.session.check_login():
            print("need to get new token for unofficial client")
            self.session.login_oauth_simple()
            cur.execute("INSERT INTO properties VALUES ('tidal_access_token', '{}')".format(self.session.access_token))
            cur.execute("INSERT INTO properties VALUES ('tidal_expiry_time', '{}')".format(self.session.expiry_time))
            cur.execute("INSERT INTO properties VALUES ('tidal_token_type', '{}')".format(self.session.token_type))
            cur.execute(
                "INSERT INTO properties VALUES ('tidal_refresh_token', '{}')".format(self.session.refresh_token))
            con.commit()

        return self.session.access_token

    def get_tidal_token(self):
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

    def update_playlists(self, playlist_name: str, playlist_id_row_id: str, tracks: set):
        track_ids = self.find_tidal_track_ids(list(tracks), self.auth)
        self.update_playlist_internal(track_ids, playlist_name, playlist_id_row_id)

    def update_playlist_internal(self, new_tracks: set, playlist_name: str, playlist_id_row_id: str):
        playlist_id = get_single_prop(playlist_id_row_id)
        if playlist_id is None:
            playlist = self.session.user.create_playlist(playlist_name, "")
            print(f"Created playlist with name {playlist_name}")
            persist_value(playlist_id_row_id, playlist.id)
            playlist_id = playlist.id
        else:
            playlist = self.session.playlist(playlist_id[0])

        no_of_tracks_in_playlist = len(playlist.tracks())
        print("Purging existing {} tracks before adding new ones".format(no_of_tracks_in_playlist))
        if no_of_tracks_in_playlist > 0:
            playlist.remove_by_indices([idx for idx in range(no_of_tracks_in_playlist)])
        time.sleep(10)

        # add tracks to list in a chunked fashion
        playlist = self.session.playlist(playlist_id)
        for idx, chunk in enumerate(chunks(list(new_tracks), 10)):
            playlist.add(chunk)
            print("Successfully added chunk {}".format(idx))

    def delete_tracks_from_playlist(self, my_client: deezer.Client, playlist_id: str, track_ids: List[str]):
        if len(track_ids) > 0:
            for chunk in numpy.array_split(numpy.array(track_ids), 10):
                my_client.request("DELETE", f"playlist/{playlist_id}/tracks", songs=comma_separated_list(chunk))

    def find_tidal_track_ids(self, parsed_tracks: list, auth: OAuth2Auth):
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
                            print("({}/{}) found track with id {} for query '{}'".format(track_idx, total_tracks,
                                                                                         track_id,
                                                                                         query_string))
                            cache_track_query(query_string, track_id)
                        else:
                            print(
                                "({}/{}) could not find track for query '{}'".format(track_idx, total_tracks,
                                                                                     query_string))
                except:
                    print("({}/{}) [{}]: {}".format(track_idx, total_tracks, song_query.status_code, song_query.text))
                time.sleep(5)

        return return_value


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


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


# ========= COMMON =========
dlf_nova_tracks = DlfNova().get_tracks()
einslive_plan_b_tracks = EinslivePlanB().get_tracks()

# ========= DEEZER =========
deezer_worker = DeezerWorker()
deezer_worker.update_playlists("Deutschlandfunk Nova Playlist", "dlf_nova_playlist_id", dlf_nova_tracks)
deezer_worker.update_playlists("1LIVE Plan B Playlist", 'einslive_plan_b_playlist_id', einslive_plan_b_tracks)

# ========= TIDAL =========
tidal_worker = TidalWorker()
tidal_worker.update_playlists("Deutschlandfunk Nova Playlist", "tidal_dlf_nova_playlist_id", dlf_nova_tracks)
tidal_worker.update_playlists("1LIVE Plan B Playlist", 'tidal_einslive_plan_b_playlist_id', einslive_plan_b_tracks)
