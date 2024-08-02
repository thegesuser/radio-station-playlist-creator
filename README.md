# Deutschlandfunk Nova Playlist Syncer

This project creates and syncs a playlist containing the last ~150 tracks from
the [Deutschlandfunk Nova Playlist Site](https://www.deutschlandfunknova.de/playlist).

## But ... why?

I like Deezer's and Tidal's curated playlists, but I found myself to be very happy with the curated music played on the
Deutschlandfunk Nova radio station. To enjoy this on the go, I created this simple script in my spare time.

## Getting started

### Prepare the `.env` file

The configuration will be done via the `.env` file.
By default, there is none.
To prepare the `.env` from the example file, simply perform the following command:

```shell
cp .env.example .env
```

### Register a Deezer App

Go to the Deezer API Page and register a new application.
You will need the `manage_library` and `delete_library` permissions.
Make sure to copy the App id and App secret.
For the Redirect url, use something where no web server is running on, for example I
chose `http://localhost:12356/callback`.
Then enter these values into the `.env` file with the text editor of your choice.

### Register the Tidal App

Go to the Tidal API Page and register a new application.
Make sure to copy the App id and App secret.
Then enter these values into the `.env` file with the text editor of your choice.

### Getting all the required packages

Make sure you have all required packages installed

```shell
pip install -r requirements.txt
```

## Running

On the first run, a `values.sqlite` will be created, where the app will store its tokens and the playlist ids into.
To perform a run, simply execute the following:

```shell
python main.py
```