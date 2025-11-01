Looking for a SOAP transfer? [Check out Bluehax](https://discord.gg/qNZePF6RxP), we do SOAP transfers in minutes for free.
## 🧼 Maidy the Cat - SOAP Management System
Maidy is a Discord.py bot built to work alongside [Soapy the Cat](https://github.com/bluehaxreloaded/soap-cat) to manage SOAP Transfers for the Nintendo 3DS family of systems.

<img width="628" height="375" alt="image" src="https://github.com/user-attachments/assets/e21f8391-94e5-4564-a8cc-4c947bbb543b" />

## Bot Features
- Create SOAP channels for helpees (those receiving SOAP transfers) through the .soap command or using a dedicated channel.
- Provide instructions to helpees to prepare for their SOAP.
- Update helpees regarding the status of their SOAP and displaying completion information by communicating with Soapy the Cat
- Provide text commands to Soapers to quickly pull up information for helpees on how to perform system actions like finding the 3DS serial number or removing a Nintendo Network ID.
- And more!

## How to Run
1. Git clone this repository into a directory
2. Ensure Python 3.13 or higher is installed
3. Run `python3.13 -m pip install -r requirements.txt` to install dependencies. Depending on your Python installation, this may be `python3` or `python`.
4. Create `constants.py` by removing `.example` from `constants.py.example`. Fill out `constants.py` with the server-specific information. Please ensure the Discord you intend to run Maidy in has all necessary channels, categories, and emotes for expected results.
5. Run `python3.13 main.py`, this may be `python3` or `python` depending on your installation.
6. If console outputs `Logged-in as YOURBOTNAME#1234`, the bot is running. Use .help for a list of commands.

> ⚠️ Please keep in mind that some of this bot's features rely on [Soapy the Cat](https://github.com/bluehaxreloaded/soap-cat), please ensure both are running concurrently.
---
### Why a cat?
Cats are cute.

### How can I get help running this bot?
We don't typically offer support running Maidy or Soapy, as we encourage most who need SOAPs to visit our community directly. Feel free to [join](https://discord.gg/qNZePF6RxP) and ask your question if you want, but there is no guarantee of a response.
