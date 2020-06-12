import os
from InstagramDownloader import Login

username = os.environ['INSTA_USERNAME']
password = os.environ['INSTA_PASSWORD']
admins = []
if 'INSTA_ADMINS' in os.environ:
    admins = os.environ['INSTA_ADMINS'].split(" ")

Login(username, password, admins)
