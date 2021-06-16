from bs4 import BeautifulSoup as bs
import requests
import emoji
from urllib.parse import urlparse
import re, datetime, os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler

import logging
log_path = f'{os.path.dirname(__file__)}/p_bot__{datetime.datetime.now().strftime("%Y_%m_%dT%H-%M-%S")}.log'
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s -- %(message)s', filename=log_path)
logger = logging.getLogger(__name__)

URL = 'https://www.e-katalog.ru/ek-item.php?idg_={}&view_=prices&order_=price'


class PriceChecker():
    def __init__(self):
        self.bot = Updater(token=os.getenv('TBOT_TOKEN'))

        dispatcher = self.bot.dispatcher
        self.users = {}

        # on different commands - answer in Telegram
        dispatcher.add_handler(CommandHandler("start", self.start))
        dispatcher.add_handler(CommandHandler("help", self.start))
        dispatcher.add_handler(CommandHandler("list", self.show_items))
        dispatcher.add_handler(CallbackQueryHandler(self.item_info, pattern='^item_*'))
        dispatcher.add_handler(CallbackQueryHandler(self.back, pattern='^back$'))
        dispatcher.add_handler(CallbackQueryHandler(self.delete, pattern='^delete_*'))
        dispatcher.add_handler(CommandHandler("add", self.add_job))

    def get_items_list(self, update, context):
        jobs = context.job_queue.get_jobs_by_name(str(update.effective_user['id']))
        if not jobs:
            return False
        else:
            keyboard = [[InlineKeyboardButton(info['item_name'], callback_data=f'item_{id}')]
                        for id, info in self.users[jobs[0].name].items()]
            reply_markup = InlineKeyboardMarkup(keyboard)
            return reply_markup

    def show_items(self, update, context):
        chat_id = str(update.message.chat_id)
        logger.info(f'[user {chat_id}] \tShow item list')
        jobs = context.job_queue.get_jobs_by_name(chat_id)
        if not jobs:
            update.message.reply_text('There are no items you tracking')
        else:
            update.message.reply_text('Tracking items:', reply_markup=self.get_items_list(update, context))

    def back(self, update, context):
        query = update.callback_query
        query.answer()
        query.edit_message_text('Tracking items:', reply_markup=self.get_items_list(update, context))

    def remove_job(self, context, chat_id, item_id):
        jobs = context.job_queue.get_jobs_by_name(chat_id)
        for job in jobs:
            if job.context == item_id:
                job.schedule_removal()

    def delete(self, update, context):
        query = update.callback_query
        chat_id = str(update.effective_user['id'])
        item_id = query.data.split('_')[1]
        logger.info(f'[user {chat_id}] \tDeleting item ({item_id})')

        del self.users[chat_id][item_id]
        self.remove_job(context, chat_id, item_id)

        query.answer()
        items_list = self.get_items_list(update, context)
        if items_list:
            query.edit_message_text('Tracking items:', reply_markup=items_list)
        else:
            query.edit_message_text('Now you are not tracking any items')

    def item_info(self, update, context):
        query = update.callback_query
        chat_id = str(update.effective_user['id'])
        item_id = query.data.split('_')[1]
        task_data = self.users[chat_id][item_id]
        logger.info(f'[user {chat_id}] \tChecking info of item ({item_id})')

        MESSAGE = f"--- {task_data['item_name']} info --- \n\n" \
                  f"Last check time: {task_data['last_check'].strftime('%d/%m/%Y, %H:%M:%S')}\n" \
                  f"Lowest price: {task_data['lowest_price']} руб.\n" \
                  f"Shop with that price: {task_data.get('shop_name', 'No shops')}"

        buttons = [[InlineKeyboardButton(text=emoji.emojize(':arrow_backward: Back', use_aliases=True), callback_data='back'),
                    InlineKeyboardButton(text=emoji.emojize('Delete :x:', use_aliases=True), callback_data=f'delete_{item_id}')]]
        keyboard = InlineKeyboardMarkup(buttons)
        query.answer()
        query.edit_message_text(text=MESSAGE, reply_markup=keyboard)

    def start(self, update, context):
        """Sends explanation on how to use the bot."""
        update.message.reply_text('Hi! Use /add <item_link> to start tracking item price.\n'
                                  'Use /list to show tracked items')

    def check_price(self, context):
        user = self.users[context.job.name]
        item_id = context.job.context
        logger.info(f'[user {context.job.name}] \tChecking price of item ({item_id})')
        user[item_id]['last_check'] = datetime.datetime.now()
        raw_html = requests.get(URL.format(item_id))
        soup = bs(raw_html.text, 'html.parser')
        prices_table = soup.find(id='item-wherebuy-table')
        if not prices_table:
            logger.info(f'[user {context.job.name}] No prices table for item ({item_id})')
            self.notify(context, message=emoji.emojize(f':warning: No prices for {user[item_id]["item_name"]} \n'
                                                       f'But I am still tracking it for you', use_aliases=True))
            return
        shops = prices_table.findAll('tr', re.compile(r'shop'), recursive=False)
        if shops:
            shops_list = []
            notify = False
            for shop in shops[:5]:
                _name = shop.find('td', 'where-buy-description').find('a', 'it-shop').text
                _price_obj = shop.find('td', 'where-buy-price')
                _price = int(re.sub('[^0-9]', '', _price_obj.contents[0].text))
                shops_list.append({'shop_name': _name, 'price': _price})
                if user[item_id]['lowest_price'] == 0 or _price <  user[item_id]['lowest_price']:
                    user[item_id]['shop_name'] = _name
                    user[item_id]['lowest_price'] = _price
                    notify = True
            if notify:
                logger.info(f'[user {context.job.name}] \tFound new lowest price of item ({item_id})')
                MESSAGE = f'Found new lowest price for { user[item_id]["item_name"]}\n'
                for idx, el in enumerate(shops_list):
                    MESSAGE += f'#{idx+1}. {el["shop_name"]} - {el["price"]} руб.\n'
                self.notify(context, message=MESSAGE)

    def notify(self, context, message):
        """Send the alarm message."""
        job = context.job
        context.bot.send_message(job.name, text=message)

    def get_item_info(self, link):
        raw_html = requests.get(link)
        soup = bs(raw_html.text, 'html.parser')
        id_obj = soup.find('meta', itemprop='sku')
        name = soup.find('div', id='top-page-title')
        if id_obj and name:
            return id_obj.get('content'), name.get('data-txt-title')
        else:
            return None

    def add_job(self, update, context):
        """Add a job to the queue."""
        try:
            chat_id = str(update.message.chat_id)
            logger.info(f'[user {chat_id}] \tTrying to add new item')
            if chat_id not in self.users:
                self.users[chat_id] = {}
            user = self.users[chat_id]
            link = context.args[0]
            link_obj = urlparse(link)
            if link_obj.netloc != 'www.e-katalog.ru':
                logger.info(f'[user {chat_id}] \tIncorrect link')
                update.message.reply_text('Incorrect link')
                return
            item_id, item_name = self.get_item_info(link)
            if not item_id or not item_name:
                logger.info(f'[user {chat_id}] \tItem not found')
                update.message.reply_text('Item not found')
                return

            if item_id in user.keys():
                logger.info(f'[user {chat_id}] \tAlready tracking item ({item_id})')
                update.message.reply_text('You are already tracking this item')
                return
            else:
                user[item_id] = {'item_name': item_name, 'lowest_price': 0, 'last_check': None}

            context.job_queue.run_repeating(self.check_price, 30 * 60, first=1, context=item_id, name=chat_id)
            update.message.reply_text('Item added to tracking')

        except IndexError:
            update.message.reply_text('Usage: /add <item_link>')

    def run_bot(self):
        """Run bot."""
        self.bot.start_polling()
        self.bot.idle()


if __name__ == '__main__':
    PriceChecker().run_bot()
