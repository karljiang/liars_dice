import random
import json
import zmq
import time
import trueskill
import os

GAME_CLOCK = 10
SAVE_FILE = 'bots.dat'
START_DICE = 5
DICE_SIDES = 6

class Game:
    def __init__(self, player0, player1):
        self.dice0 = START_DICE
        self.dice1 = START_DICE
        self.player0 = player0
        self.player1 = player1
        self.time0 = GAME_CLOCK
        self.time1 = GAME_CLOCK
        self.initialize()
        self.waiting_on = set()

    def initialize(self):
        self.cup0 = [ random.randint(1,DICE_SIDES) for i in range(self.dice0) ]
        self.cup1 = [ random.randint(1,DICE_SIDES) for i in range(self.dice1) ]
        self.history = []
        self.turn = random.randint(0,1)
        self.ones_valid = True
        self.last_time = time.time()

    def reinit(self, winner):
        if winner == self.player0:
            self.dice1 -= 1
            if self.dice1 == 0:
                return True
        else:
            self.dice0 -= 1
            if self.dice0 == 0:
                return True

        self.waiting_on = set([self.player0, self.player1])
        self.initialize()
        return False

    def current_player(self):
        if self.turn == 0:
            return self.player0
        else:
            return self.player1

    def play_valid(self, k, n):
        last_play = [0, 0] if not self.history else self.history[-1]
       
        if k < 1 or n < 1 or n > 6:
            return False
        if k == last_play[0] and n > last_play[1]:
            return True
        if k > last_play[0]:
            return True
        return False

    def check_expired(self):
        time_now = time.time()
        elapsed = time_now - self.last_time
        if self.turn == 0:
            self.time0 -= elapsed
        else:
            self.time1 -= elapsed

        self.last_time = time_now
        if self.time0 <= 0:
            return self.player1
        if self.time1 <= 0:
            return self.player0
    
        return None

    def play(self, k, n):
        if k == 0 and n == 0:
            if not self.history:
                return -1
            last_play = self.history[-1]
            pip = last_play[1]
            if self.ones_valid:
                self.cup0 = [pip if x == 1 else x for x in self.cup0]
                self.cup1 = [pip if x == 1 else x for x in self.cup1]
            
            total = sum([1 if x == pip else 0 for x in self.cup0]) + \
                    sum([1 if x == pip else 0 for x in self.cup1])

            if total < last_play[0]:
                return self.player0 if self.turn == 0 else self.player1
            else:
                return self.player1 if self.turn == 0 else self.player0

        if not self.play_valid(k, n):
            return -1
        
        winner = self.check_expired()
        if winner is not None:
            return winner

        self.turn = 1 - self.turn

        if n == 1 and not self.history:
            self.ones_valid = False
        self.history.append([k, n])
        return None

class Server:
    def __init__(self):
        self.active_games = dict()
        self.bots = dict()
        self.ratings = dict()
        self.warns = dict()
        self.waiting_bot = None

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.ROUTER)
        self.socket.bind("tcp://*:5555")

        self.load_ratings()

    def send(self, bot_id, msg):
        self.socket.send(bot_id, zmq.SNDMORE)
        self.socket.send("", zmq.SNDMORE)
        self.socket.send(msg)

    def send_player0(self, game, winner, done=False):
        msg = json.dumps({"dice": game.cup0,
                          "history": game.history,
                          "ones_valid": game.ones_valid,
                          "winner": winner,
                          "game_complete": done,
                          "opponent": self.bots[game.player1],
                          "oppenent_dice_num": game.dice1,
                          "opponent_dice": game.cup1 if winner else None})
        self.send(game.player0, msg)

    def send_player1(self, game, winner, done=False):
        msg = json.dumps({"dice": game.cup1,
                          "history": game.history,
                          "ones_valid": game.ones_valid,
                          "winner": winner,
                          "game_complete": done,
                          "opponent": self.bots[game.player0],
                          "oppenent_dice_num": game.dice0,
                          "opponent_dice": game.cup0 if winner else None})
        self.send(game.player1, msg)

    def send_game(self, game, winner=None, done=False):
        if game.turn == 0:
            self.send_player0(game, winner)
        else:
            self.send_player1(game, winner)

    def send_warn(self, bot_id, error):
        self.send(bot_id, json.dumps({"error": error}))

    def scan_games(self):
         games = self.active_games.values()
         for game in games:
            if game not in self.active_games.values():
                continue
            winner = game.check_expired()
            if winner is not None:
                if winner == game.player0:
                    self.send_player0(game, winner=self.bots[winner], done=True)
                if winner == game.player1:
                    self.send_player1(game, winner=self.bots[winner], done=True)

                self.update_ratings(game, winner)
                del self.active_games[game.player0]
                del self.active_games[game.player1]

    def init_bot(self, bot_id, uuid, rating=trueskill.Rating()):
        uuid = str(uuid)
        self.bots[uuid] = bot_id
        self.warns[uuid] = 0
        self.ratings[uuid] = rating

    def load_ratings(self):
        if not os.path.isfile(SAVE_FILE):
            return
        with open(SAVE_FILE, 'r') as f:
            while True:
                line = f.readline()
                if not line:
                    break
                [name, uuid, mu, sigma] = line.split(',')
                self.init_bot(name, uuid, trueskill.Rating(float(mu), float(sigma)))

    def save_ratings(self):
        with open(SAVE_FILE, 'w') as f:
            for bot_id in self.ratings.keys():
                rating = self.ratings[bot_id]
                name = self.bots[bot_id]
                f.write("%s,%s,%s,%s\n" % (name, bot_id, rating.mu, rating.sigma))

    def update_ratings(self, game, winner):
        if game.player0 == winner:
            loser = game.player1
        else:
            loser = game.player0

        self.ratings[winner], self.ratings[loser] = trueskill.rate_1vs1(self.ratings[winner],
                                                                        self.ratings[loser])
        self.save_ratings()
        
    def run(self):
        self.scan_games()
        bot_id = self.socket.recv()
        self.socket.recv()
        msg = self.socket.recv()
        
        if msg == 'register':
            if ',' in bot_id or bot_id in self.bots.values():
                self.send(bot_id, "0")
            else:
                uuid = random.getrandbits(31)
                print "registering bot %s -> %s" % (bot_id, uuid)
                self.init_bot(bot_id, uuid)
                self.save_ratings()
                self.send(bot_id, str(uuid))
        elif msg == 'start':
            if bot_id not in self.bots.keys():
                self.send_warn(bot_id, "your bot id %s is not registered" % bot_id)
            elif bot_id in self.active_games.keys():
                self.send_warn(bot_id, "your bot is already locked in combat")
            elif not self.waiting_bot:
                self.waiting_bot = bot_id
            else:
                game = Game(self.waiting_bot, bot_id)
                print "starting game between %s and %s!" % (self.bots[self.waiting_bot], self.bots[bot_id])
                self.active_games[self.waiting_bot] = game
                self.active_games[bot_id] = game
                self.waiting_bot = None
                self.send_game(game)
        elif msg == 'next':
            if bot_id not in self.active_games.keys():
                self.send_warn(bot_id, "your bot is not currently in a game, or it has completed")
                return

            game = self.active_games[bot_id]
            if bot_id not in game.waiting_on:
                self.send_warn(bot_id, "you have already registered for the next round, please by patient as the other bot joins")
                return

            game.waiting_on.remove(bot_id)
            if not game.waiting_on:
                self.send_game(game)

        elif msg == 'ping':
            self.send(bot_id, "pong")
        else:
            nums = msg.split(',')
            if bot_id not in self.active_games.keys():
                self.send_warn(bot_id, "your bot is not currently in a game, or it has completed")
            elif len(nums) != 2:
                self.send_warn(bot_id, "must send 2 comma separated values")
            elif not nums[0].isdigit() or not nums[1].isdigit():
                self.send_warn(bot_id, "values sent must be non-negative integers")
            else:
                game = self.active_games[bot_id]
                if game.current_player() != bot_id or game.waiting_on:
                    self.send_warn(bot_id, "it is not currently your turn to play")
                    return
                
                ret = game.play(int(nums[0]), int(nums[1]))
                if ret == -1:
                    self.send_warn(bot_id, "%s: that move is illegal" % msg)
                elif ret is None:
                    print "[%s] -> bids %s %ss" % (self.bots[bot_id], nums[0], nums[1])
                    self.send_game(game)
                else:
                    print "[%s] -> bullshit!" % self.bots[bot_id]
                    done = game.reinit(ret)
                    print "%s wins the round!" % self.bots[ret]
                    self.send_player0(game, winner=self.bots[ret], done=done)
                    self.send_player1(game, winner=self.bots[ret], done=done)
                    if done:
                        print "%s wins the game!" % self.bots[ret]
                        self.update_ratings(game, ret)
                        del self.active_games[game.player0]
                        del self.active_games[game.player1]

server = Server()
while True:
    server.run()
