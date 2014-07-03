define([
	"dojo/_base/declare",
	"dojo/dom",
	"dojo/dom-construct",
	"dojo/dom-class",
	"dojo/dom-style",
	"dojo/on",
	"dojo/query",
	"dojo/string",
	"dojo/date/locale",
	"dojo/date/stamp",
	"dojo/text!/static/tmpl/user.html",
	"dojo/text!/static/tmpl/message.html",
	"dojo/text!/static/tmpl/hub.html",
	"dojo/NodeList-dom",
	"dojo/domReady!"
], function( 
	declare,
	dom,
	domConstruct,
	domClass,
	domStyle,
	on,
	query,
	string,
	locale,
	stamp,
	user_template,
	message_template,
	hub_template
) {
	var startsWith = function(start, text) {
		return text.substring(0, start.length)===start;
	}

	var get_sel_text = function() {
		var txt = '';
		if (window.getSelection) {
			txt = window.getSelection();
		} else if (document.getSelection) {
			txt = document.getSelection();
		} else if (document.selection) {
			txt = document.selection.createRange().text;
		} else return;

		return txt.toString();
	};

	var 	chat_ws = null,
		init_reconnect_timer = null,
		fist_update = true,
		my_user = null,
		current_hub = "",
		current_hub_url = "",
		change_hub = false,
		revert_chat_order = false,
		send_message_enter = false;
	return declare(null, {
		constructor: function() {
			var self = this;

			self.chat_table = dom.byId("chat_table");
			self.chat_users = dom.byId("chat_users");
			self.message_textarea =  dom.byId("message_textarea");
			self.chat_hubs = dom.byId("chat_hubs_list");
			self.lock_ui();
			self.init_tabs();
			self.init_settings();
			self.websocket_init();
			on(dom.byId("chat_send_button"), "click", function(evt) {self.submit_message(evt)});
			on(self.message_textarea, "keydown", function(evt) {
				if (send_message_enter) {
					if (evt.ctrlKey == false && evt.keyCode == 13 && dom.byId("chat_send_button").disabled == false) {
						self.submit_message(evt);
					} else if (evt.ctrlKey == false && evt.keyCode == 13 && dom.byId("chat_send_button").disabled == true) {
						evt.preventDefault();
						return false;
					} else if (evt.ctrlKey == true && evt.keyCode == 13) {
						var val = self.message_textarea.value;
						if (typeof self.message_textarea.selectionStart == "number" && typeof self.message_textarea.selectionEnd == "number") {
							var start = self.message_textarea.selectionStart;
							self.message_textarea.value = val.slice(0, start) + "\n" + val.slice(self.message_textarea.selectionEnd);
							self.message_textarea.selectionStart = self.message_textarea.selectionEnd = start + 1;
						} else if (document.selection && document.selection.createRange) {
							self.message_textarea.focus();
							var range = document.selection.createRange();
							range.text = "\r\n";
							range.collapse(false);
							range.select();
						}
					}
				} else {
					if (evt.ctrlKey && evt.keyCode == 13 && dom.byId("chat_send_button").disabled == false) {
						self.submit_message(evt);
					}
				}
			});

			setInterval(function () {
				if (chat_ws.readyState == WebSocket.OPEN) {
					chat_ws.send(JSON.stringify({
						type:"all_hubs"
					}));
				}
			}, 1000*60);

			setInterval(self.websocket_ping, 1000*25);
		},
		init_tabs: function() {
			on(dom.byId("chat_users_tab"), "click", function() {
				domClass.add(this, "active");
				domClass.remove(dom.byId("chat_hubs_tab"), "active");
				domClass.remove(dom.byId("chat_settings_tab"), "active");

				domStyle.set(dom.byId("chat_hubs"), {display: "none"});
				domStyle.set(dom.byId("chat_users"), {display: "block"});
				domStyle.set(dom.byId("chat_settings"), {display: "none"});
			});
			on(dom.byId("chat_hubs_tab"), "click", function() {
				domClass.add(this, "active");
				domClass.remove(dom.byId("chat_users_tab"), "active");
				domClass.remove(dom.byId("chat_settings_tab"), "active");

				domStyle.set(dom.byId("chat_users"), {display: "none"});
				domStyle.set(dom.byId("chat_hubs"), {display: "block"});
				domStyle.set(dom.byId("chat_settings"), {display: "none"});
			});
			on(dom.byId("chat_settings_tab"), "click", function() {
				domClass.add(this, "active");
				domClass.remove(dom.byId("chat_users_tab"), "active");
				domClass.remove(dom.byId("chat_hubs_tab"), "active");

				domStyle.set(dom.byId("chat_users"), {display: "none"});
				domStyle.set(dom.byId("chat_hubs"), {display: "none"});
				domStyle.set(dom.byId("chat_settings"), {display: "block"});
			});
		},
		init_settings: function() {
			var self = this;
			on(dom.byId("settings_revert_chat_order"), "change", function(evt) {
				revert_chat_order = this.checked;
				self.apple_revert_chat_order();
				self.send_settings();
			});

			on(dom.byId("settings_send_message_enter"), "change", function(evt) {
				send_message_enter = this.checked;
				self.send_settings();
			});

			var tags = {
				"b": "[b]${text}[/b]",
				"i": "[i]${text}[/i]",
				"u": "[u]${text}[/u]",
				"s": "[s]${text}[/s]",
				"img": "[img][/img]",
				"quote": "[quote]${text}[/quote]",
				"size": "[size=15]${text}[/size]",
				"color": "[color=red]${text}[/color]",
				"center":"[center]${text}[/center]",
				"code":"[code]${text}[/code]"
			};
			var	tags_help = dom.byId("tags_help"),
				tag_node = null; 
			for (key in tags) {
				tag_node = domConstruct.toDom("<button type='button' class='btn btn-default btn-xs' data-patern='"+tags[key]+"'>"+key+"</button>");
				on(tag_node, "click", function() {
					self.message_textarea.value += string.substitute(this.getAttribute("data-patern"), {"text": get_sel_text()});
				});
				domConstruct.place(tag_node, tags_help, "last");
				
			}
		},
		apple_revert_chat_order: function(revert_message){
			var self = this;
			if (revert_chat_order) {
				query(".input-row").style({
					order: "2",
					"-webkit-order": "2",
					"-ms-flex-order": "2"
				})
				query(".chat-messages").style({
					order: "1",
					"-webkit-order": "1",
					"-ms-flex-order": "1"
				})
			} else {
				query(".input-row").style({
					order: "1",
					"-webkit-order": "1",
					"-ms-flex-order": "1"
				})
				query(".chat-messages").style({
					order: "2",
					"-webkit-order": "2",
					"-ms-flex-order": "2"
				})
			}
			if (revert_message==null) {
				var lines = dojo.query("#chat_table tr").reverse();
				lines.forEach(function(element){
					domConstruct.place(element, self.chat_table, "last");
				});
			}
			if (revert_chat_order) {
				dom.byId("chat_messages").scrollTop = 30000;
			} else {
				dom.byId("chat_messages").scrollTop = 0;
			}
		},
		send_settings: function() {
			if (chat_ws.readyState == WebSocket.OPEN) {
				chat_ws.send(JSON.stringify({
					type:"settings",
					settings: {
						"revert_chat_order": revert_chat_order,
						"send_message_enter": send_message_enter
					}
				}));
			}
		},
		parse_settings: function() {
			if (my_user.settings != null) {
				if (my_user.settings.revert_chat_order != null) {
					revert_chat_order = my_user.settings.revert_chat_order;
					//console.log(revert_chat_order)
					dom.byId("settings_revert_chat_order").checked = revert_chat_order;
					this.apple_revert_chat_order(true);
				}
				if (my_user.settings.send_message_enter != null) {
					send_message_enter = my_user.settings.send_message_enter;
					//console.log(revert_chat_order)
					dom.byId("settings_send_message_enter").checked = send_message_enter;
				}
			}
		},
		websocket_init: function () {
			var self = this;
			chat_ws = new WebSocket("ws://"+window.location.host+"/start-chat?"+current_hub_url);
			chat_ws.onopen = function() { self.onopen(); };
			chat_ws.onmessage = function(evt) { self.onmessage(evt); };
			chat_ws.onclose = function(evt) { self.onmessage(evt); };
		},
		websocket_ping: function() {
			if (chat_ws.readyState == WebSocket.OPEN) {
				chat_ws.ping();
			}
		},
		onopen: function() {
			console.log("Open socket");
			var self = this;
			self.lock_ui("Ожидайте 5 секунд");
			setTimeout(function() {
				self.unlock_ui();
			}, 7000);
		},
		onmessage: function(evt) {
			var self = this;
			if (evt.type == "close") {
				console.log("close event");
				this.lock_ui();
				this.clean_all_messages();
				this.reconnect();
				return;
			}
			data = JSON.parse(evt.data);
			//console.log(data.type);
			if (data.type=="all_users") {
				console.log("all_users");
				query("#chat_users > *").forEach(domConstruct.destroy);
				for (var i=0; i<data.users.length; i++) {
					if (data.users[i].iam != null) {
						my_user = data.users[i];
						current_hub = my_user.hub;
						self.parse_settings();
					}
					var new_user_dom = self.create_new_user(data.users[i])
					domConstruct.place(new_user_dom, self.chat_users, "last");
					if (data.users[i].iam != null) {
						domClass.add(new_user_dom, "list-group-item-info");
					}
				}
			} else if (data.type=="last_messages") {
				if (fist_update) {
					for (var i=0; i<data.messages.length; i++) {
						domConstruct.place(self.create_new_message(data.messages[i]), self.chat_table, revert_chat_order ? "first" : "last");
					}
					fist_update==false;
					if (revert_chat_order) {
						setTimeout(function() {
							dom.byId("chat_messages").scrollTop = 30000;
						}, 200);
					}
				}
			} else if (data.type=="all_hubs") {
				console.log("all_hubs");
				query("#chat_hubs_list > *").forEach(domConstruct.destroy);
				sorted_hubs = data.hubs.sort(function(a,b){return b.users-a.users})

				for (var i=0; i<data.hubs.length; i++) {
					domConstruct.place(self.create_new_hub(sorted_hubs[i]), self.chat_hubs, "last");
				}
				query("#chat_hubs_list > *").forEach(function(hub){
					if (current_hub == hub.getAttribute("data-name")) {
						domClass.add(hub, "list-group-item-success");
					} else {
						domClass.remove(hub, "list-group-item-success");
					}
				});
			} else if (data.type=="new_user") {
				console.log("new_user");
				if (dom.byId("chat_user_"+data.user.id)==null) {
					domConstruct.place(self.create_new_user(data.user), self.chat_users, "last");
				}
			} else if (data.type=="del_user") {
				console.log("del_user");
				domConstruct.destroy(dom.byId("chat_user_"+data.user_id));
				
			} else if (data.type=="new_message") { 
				console.log("new_message");
				var after_scroll = false;
				if (revert_chat_order && dom.byId("chat_messages").scrollHeight == dom.byId("chat_messages").scrollTop+dom.byId("chat_messages").clientHeight) {
					after_scroll = true;
				}
				if (self.chat_table.firstChild!=null) {
					domConstruct.place(self.create_new_message(data.message), self.chat_table, revert_chat_order ? "last" : "first");
				}
				if (self.chat_table.childNodes.length > 150) {
					if (revert_chat_order) {
						domConstruct.destroy(self.chat_table.firstChild);
					} else {
						domConstruct.destroy(self.chat_table.lastChild);
					}
				}
				if (after_scroll) {
					dom.byId("chat_messages").scrollTop = 30000;
				}
			} else if (data.type=="delete_message") { 
				console.log("delete_message");
				query("#chat_table tr").forEach(function(element){
					if (element.getAttribute("data-user-id")==data.user_id&&element.getAttribute("data-datetime")==data.datetime) {
						domConstruct.destroy(element);
					}
				});
				
			} else if (data.type=="logout") {
				window.location.href = "/logout";
			}
		},
		lock_ui: function (text) {
			if (text != null) {
				dom.byId("chat_send_button").innerHTML = text;
			}
			dom.byId("chat_send_button").disabled = true;
			//dom.byId("message_textarea").disabled = true;
		},
		unlock_ui: function () {
			dom.byId("chat_send_button").disabled = false;
			dom.byId("chat_send_button").innerHTML = "Отправить";
			//dom.byId("message_textarea").disabled = false;
		},
		clean_all_messages: function() {
			query("#chat_table tr").forEach(domConstruct.destroy);
		},
		reconnect: function () {
			var self = this;
			if (change_hub==true) {
				if (chat_ws.readyState == WebSocket.CLOSED) {
					self.websocket_init();
				}
				change_hub = false;
			}else if (init_reconnect_timer == null) {
				init_reconnect_timer = setInterval(function(){
					if (chat_ws.readyState == WebSocket.CLOSED) {
						self.websocket_init();
					} else if (chat_ws.readyState == WebSocket.OPEN) {
						clearInterval(init_reconnect_timer);
						init_reconnect_timer = null;
					}
				}, 2000);
			}
		},
		create_new_user: function(user) {
			var self = this;
			var user = domConstruct.toDom(string.substitute(user_template, user));
			on(user, "click", function() {
				self.put_user_name(this);
			});
			return user;
		},
		create_new_message: function(message) {
			var self = this;
			var select_text = startsWith(my_user.name, message.text) ? "active" : "";

			var moder_tools = "";
			if (my_user.ismoderator) {
				moder_tools = string.substitute("<td onclick='ChatApp.delete_message(this)'>Remove</td>");
			}

			var message = domConstruct.toDom(string.substitute(message_template, {
				name: message.user.name,
				avatar: message.user.avatar == null ? "" : message.user.avatar,
				text: message.text,
				tr_class: select_text,
				moder_tools: moder_tools,
				datetime: message.datetime,
				user_id: message.user.id,
				format_datetime: locale.format(stamp.fromISOString(message.datetime),{ formatLength: "short"})
			}));
			return message;
		},
		put_user_name: function(user_dom) {
			self.message_textarea.value += user_dom.getAttribute("data-name")+", ";
			self.message_textarea.focus();
			self.message_textarea.setSelectionRange(1000,1000);
		},
		create_new_hub: function(hub) {
			var self = this;
			var hub = domConstruct.toDom(string.substitute(hub_template, hub));
			on(hub, "click", function() {
				if (change_hub==false) {
					self.select_hub(this);
				}
			});
			return hub;
		},
		select_hub: function(hub) {
			current_hub = hub.getAttribute("data-name");
			current_hub_url = "hub="+current_hub;
			change_hub = true;
			chat_ws.close();
		},
		submit_message: function(evt) {
			var self = this;
			if (self.message_textarea.value=="") {
				return;
			}

			evt.stopPropagation();
			evt.preventDefault();
			self.lock_ui("Ожидайте 7 секунд");
			chat_ws.send(JSON.stringify({
				type:"new_message", 
				message:self.message_textarea.value
			}));
			self.message_textarea.value = "";
			setTimeout(function() {
				self.unlock_ui();
			}, 7000);
			
			//if (permission_notification.toLowerCase()=="default") {
			//	Notification.requestPermission( function(result) { permission_notification = result  } );
			//}
		},
		delete_message: function(element) {
			if (confirm("You are sure?")) {
				chat_ws.send(JSON.stringify({
					type:"delete_message", 
					user_id: element.parentNode.getAttribute("data-user-id"),
					datetime: element.parentNode.getAttribute("data-datetime")
				}));
			}
		}
	});
});