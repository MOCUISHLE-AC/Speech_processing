import math
import time
import random
import os, sys

from tqdm import tqdm
import torch
import torch.nn as nn
from torch.autograd import Variable
#新加
from torch.utils.tensorboard import SummaryWriter
import shutil

op="test"
# Feed-forward Neural Network Language Model
class FNN_LM(nn.Module):
  def __init__(self, nwords, emb_size, hid_size, num_hist,droupout=0.2):
    super(FNN_LM, self).__init__()
    self.embedding = nn.Embedding(nwords, emb_size)
    self.fnn = nn.Sequential(
      # nn.Linear(num_hist*emb_size, hid_size),
      # nn.Tanh(),
      # nn.Linear(hid_size, nwords)
      nn.Linear(num_hist*emb_size, hid_size),
      #防止训练过度
      nn.Dropout(droupout),
      #激活函数
      nn.Tanh(),
      nn.Linear(hid_size, hid_size*4),
      nn.Dropout(droupout),
      nn.Tanh(),
      nn.Linear(hid_size*4, hid_size),
      nn.Dropout(droupout),
      nn.Tanh(),
      nn.Linear(hid_size, nwords)
    )

  def forward(self, words):
    emb = self.embedding(words)      # 3D Tensor of size [batch_size x num_hist x emb_size]
    feat = emb.view(emb.size(0), -1) # 2D Tensor of size [batch_size x (num_hist*emb_size)]
    logit = self.fnn(feat)           # 2D Tensor of size [batch_size x nwords]

    return logit

model = torch.load('model_epoch25.pt')
print(model)
#Todo::根据上文前两个词预测后文
N = 2 # The length of the n-gram
EMB_SIZE = 128 # The size of the embedding
HID_SIZE = 128 # The size of the hidden layer

USE_CUDA = torch.cuda.is_available()

# Functions to read in the corpus
# NOTE: We are using data from the Penn Treebank, which is already converted
#       into an easy-to-use format with "<unk>" symbols. If we were using other
#       data we would have to do pre-processing and consider how to choose
#       unknown words, etc.
w2i = {}
S = w2i["<s>"] = 0
UNK = w2i["<unk>"] = 1
def get_wid(w2i, x, add_vocab=True):
  if x not in w2i:
    if add_vocab:
      w2i[x] = len(w2i)
    else:
      return UNK
  return w2i[x]
def read_dataset(filename, add_vocab):
  with open(filename, "r") as f:
    for line in f:
      yield [get_wid(w2i, x, add_vocab) for x in line.strip().split(" ")]

# Read in the data
train = list(read_dataset("../data/ptb-text/train.txt", add_vocab=True))
dev = list(read_dataset("../data/ptb-text/valid.txt", add_vocab=False))
i2w = {v: k for k, v in w2i.items()}
nwords = len(w2i)
print(nwords)

# Initialize the model and the optimizer
#model = FNN_LM(nwords=nwords, emb_size=EMB_SIZE, hid_size=HID_SIZE, num_hist=N)
if USE_CUDA:
  model = model.cuda()
optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

# convert a (nested) list of int into a pytorch Variable
def convert_to_variable(words):
  var = Variable(torch.LongTensor(words))
  if USE_CUDA:
    var = var.cuda()

  return var

# A function to calculate scores for one value
def calc_score_of_histories(words):
  # This will change from a list of histories, to a pytorch Variable whose data type is LongTensor
  words_var = convert_to_variable(words)
  logits = model(words_var)
  return logits

# Calculate the loss value for the entire sentence
def calc_sent_loss(sent):
  # The initial history is equal to end of sentence symbols
  hist = [S] * N
  # Step through the sentence, including the end of sentence token
  all_histories = []
  all_targets = []
  for next_word in sent + [S]:
    all_histories.append(list(hist))
    all_targets.append(next_word)
    hist = hist[1:] + [next_word]

  logits = calc_score_of_histories(all_histories)
  loss = nn.functional.cross_entropy(logits, convert_to_variable(all_targets), size_average=False)

  return loss

MAX_LEN = 100
# Generate a sentence
def generate_sent():
  hist = [S] * N
  sent = []
  while True:
    logits = calc_score_of_histories([hist])
    prob = nn.functional.softmax(logits, 1)
    multinom = prob.multinomial(1)
    next_word = multinom.data.item()
    if next_word == S or len(sent) == MAX_LEN:
      break
    sent.append(next_word)
    hist = hist[1:] + [next_word]
  return sent

last_dev = 1e20
best_dev = 1e20

dir_path = './logs'

try:
    shutil.rmtree(dir_path)
except OSError as e:
    print("Error: %s : %s" % (dir_path, e.strerror))

if not os.path.exists("./logs"):
    os.makedirs("./logs")
writer = SummaryWriter('./logs')
if op=="train":
  for epoch in range(30):
    # Perform training
    random.shuffle(train)
    # set the model to training mode
    model.train()
    train_words, train_loss = 0, 0.0
    start = time.time()
    print(f'Starting training epoch {epoch+1} over {len(train)} sentences')
    for sent_id, sent in tqdm(enumerate(train)):
      my_loss = calc_sent_loss(sent)
      train_loss += my_loss.data
      train_words += len(sent)
      optimizer.zero_grad()
      my_loss.backward()
      optimizer.step()
      if (sent_id+1) % 5000 == 0:
        print("--finished %r sentences (word/sec=%.2f)" % (sent_id+1, train_words/(time.time()-start)))
    print("iter %r: train loss/word=%.4f, ppl=%.4f (word/sec=%.2f)" % (epoch, train_loss/train_words, math.exp(train_loss/train_words), train_words/(time.time()-start)))
    #绘图
    writer.add_scalar("train loss/word", train_loss/train_words, epoch)

    #验证集
    # Evaluate on dev set
    # set the model to evaluation mode
    model.eval()
    dev_words, dev_loss = 0, 0.0
    start = time.time()
    for sent_id, sent in enumerate(dev):
      my_loss = calc_sent_loss(sent)
      dev_loss += my_loss.data
      dev_words += len(sent)

    # Keep track of the development accuracy and reduce the learning rate if it got worse
    if last_dev < dev_loss:
      optimizer.param_groups[0]['lr']/=2
    last_dev = dev_loss

    # Keep track of the best development accuracy, and save the model only if it's the best one
    if best_dev > dev_loss:
      torch.save(model, "model_epoch25.pt")

      best_dev = dev_loss

    # Save the model
    print("epoch %r: dev loss/word=%.4f, ppl=%.4f (word/sec=%.2f)" % (epoch, dev_loss/dev_words, math.exp(dev_loss/dev_words), dev_words/(time.time()-start)))
    writer.add_scalar("dev loss/word", dev_loss/dev_words, epoch)
    # Generate a few sentences
    for _ in range(5):
      sent = generate_sent()
      print(" ".join([i2w[x] for x in sent]))

  writer.close()
else:
  mytest = list(read_dataset("../data/ptb-text/mytest.txt", add_vocab=False))
  model.eval()
  for send_id, sent in enumerate(mytest):
    loss = calc_sent_loss(sent)
    print("id=%d loss=%f ppl=%f"%(send_id,loss.data, math.exp(loss.data/len(sent))))