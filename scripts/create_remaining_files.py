#!/usr/bin/env python3
"""Create all remaining files for agent_remote_server"""

from pathlib import Path

def write_file(path: str, content: str):
    """Write content to file, creating directories as needed"""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"✓ Created {filepath} ({len(content)} bytes)")

# All file contents defined as constants to avoid escaping issues

SERVER_RS = r'''use crate::auth::{AuthToken, TokenManager};
use crate::websocket::handle_websocket;
use anyhow::Result;
use axum::extract::ws::WebSocketUpgrade;
use axum::extract::{Query, State as AxumState};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use collections::HashMap;
use futures::future::AbortHandle;
use gpui::{Context, Task};
use log::{error, info};
use parking_lot::RwLock;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::Arc;
use tokio::net::TcpListener;

/// Configuration for the remote agent server
#[derive(Debug, Clone)]
pub struct ServerConfig {
    /// Port to bind to (0 = auto-select)
    pub port: u16,
    /// Whether to bind to all interfaces (0.0.0.0) or just localhost
    pub bind_all_interfaces: bool,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            port: 0, // Auto-select
            bind_all_interfaces: true,
        }
    }
}

/// Current state of the running server
#[derive(Debug, Clone)]
pub struct ServerState {
    /// Local address the server is bound to
    pub local_addr: SocketAddr,
    /// Authentication token
    pub auth_token: AuthToken,
    /// Full pairing URL (ws://...)
    pub pairing_url: String,
}

/// Internal server state shared across handlers
#[derive(Clone)]
struct InternalServerState {
    token_manager: Arc<RwLock<TokenManager>>,
}

/// Remote agent server that handles HTTP and WebSocket connections
pub struct RemoteAgentServer {
    config: ServerConfig,
    state: Option<Arc<ServerState>>,
    token_manager: Arc<RwLock<TokenManager>>,
    abort_handle: Option<AbortHandle>,
}

impl RemoteAgentServer {
    pub fn new(config: ServerConfig, _cx: &mut Context<Self>) -> Self {
        Self {
            config,
            state: None,
            token_manager: Arc::new(RwLock::new(TokenManager::new())),
            abort_handle: None,
        }
    }

    /// Start the server
    pub fn start(&mut self, cx: &mut Context<Self>) -> Task<()> {
        let config = self.config.clone();
        let token_manager = Arc::clone(&self.token_manager);

        cx.spawn(async move |_this, _cx| {
            match Self::run_server(config, token_manager.clone()).await {
                Ok((server_state, _abort_handle)) => {
                    info!("Server started on {}", server_state.local_addr);
                    info!("Pairing URL: {}", server_state.pairing_url);
                    // TODO: Store state and abort_handle
                }
                Err(e) => {
                    error!("Failed to start server: {:?}", e);
                }
            }
        })
    }

    /// Stop the server
    pub fn stop(&mut self) {
        if let Some(handle) = self.abort_handle.take() {
            handle.abort();
            info!("Server stopped");
        }
        self.state = None;
    }

    /// Get current server state
    pub fn state(&self) -> Option<Arc<ServerState>> {
        self.state.clone()
    }

    /// Run the axum server
    async fn run_server(
        config: ServerConfig,
        token_manager: Arc<RwLock<TokenManager>>,
    ) -> Result<(ServerState, AbortHandle)> {
        let ip = if config.bind_all_interfaces {
            IpAddr::V4(Ipv4Addr::UNSPECIFIED)
        } else {
            IpAddr::V4(Ipv4Addr::LOCALHOST)
        };

        let addr = SocketAddr::new(ip, config.port);
        let listener = TcpListener::bind(addr).await?;
        let local_addr = listener.local_addr()?;

        info!("Server binding to {}", local_addr);

        // Build the pairing URL
        let auth_token = token_manager.read().current_token().clone();
        let pairing_url = build_pairing_url(local_addr, &auth_token)?;

        let server_state = ServerState {
            local_addr,
            auth_token: auth_token.clone(),
            pairing_url,
        };

        let internal_state = InternalServerState { token_manager };

        // Build the router
        let app = Router::new()
            .route("/", get(index_handler))
            .route("/ws", get(websocket_handler))
            .route("/api/health", get(health_handler))
            .with_state(internal_state);

        // Spawn the server in the background
        let (abort_handle, abort_registration) = AbortHandle::new_pair();
        tokio::spawn(async move {
            let server_future = axum::serve(listener, app).into_future();
            let result = futures::future::Abortable::new(server_future, abort_registration).await;
            match result {
                Ok(Ok(())) => info!("Server completed successfully"),
                Ok(Err(e)) => error!("Server error: {:?}", e),
                Err(_) => info!("Server aborted"),
            }
        });

        Ok((server_state, abort_handle))
    }
}

/// Build the WebSocket pairing URL
fn build_pairing_url(addr: SocketAddr, token: &AuthToken) -> Result<String> {
    // Get local IP address for LAN access
    let ip = if addr.ip().is_unspecified() {
        get_local_ip()?
    } else {
        addr.ip()
    };

    Ok(format!("ws://{}:{}?token={}", ip, addr.port(), token))
}

/// Get the local IP address for LAN access
fn get_local_ip() -> Result<IpAddr> {
    // Try to get the local IP by connecting to a remote address
    // This doesn't actually send data, just determines which interface would be used
    let socket = std::net::UdpSocket::bind("0.0.0.0:0")?;
    socket.connect("8.8.8.8:80")?;
    let local_addr = socket.local_addr()?;
    Ok(local_addr.ip())
}

/// Root handler - serves the web UI
async fn index_handler() -> Html<&'static str> {
    Html(include_str!("web_ui/index.html"))
}

/// WebSocket upgrade handler
async fn websocket_handler(
    ws: WebSocketUpgrade,
    Query(params): Query<HashMap<String, String>>,
    AxumState(state): AxumState<InternalServerState>,
) -> Response {
    // Validate authentication token
    let token = match params.get("token") {
        Some(t) => AuthToken::from_string(t.clone()),
        None => {
            return (
                axum::http::StatusCode::UNAUTHORIZED,
                "Missing authentication token",
            )
                .into_response();
        }
    };

    if !state.token_manager.read().validate(&token) {
        return (
            axum::http::StatusCode::UNAUTHORIZED,
            "Invalid authentication token",
        )
            .into_response();
    }

    // Upgrade to WebSocket
    ws.on_upgrade(move |socket| handle_websocket(socket))
}

/// Health check endpoint
async fn health_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "service": "agent-remote-server"
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_server_config_default() {
        let config = ServerConfig::default();
        assert_eq!(config.port, 0);
        assert!(config.bind_all_interfaces);
    }

    #[test]
    fn test_build_pairing_url() {
        let addr = "192.168.1.100:8080".parse().unwrap();
        let token = AuthToken::from_string("test123".to_string());
        let url = build_pairing_url(addr, &token).unwrap();
        assert_eq!(url, "ws://192.168.1.100:8080?token=test123");
    }
}
'''

WEBSOCKET_RS = r'''use anyhow::{Result, anyhow};
use axum::extract::ws::{Message, WebSocket};
use futures::{SinkExt, StreamExt};
use log::{debug, error, info, warn};
use serde::{Deserialize, Serialize};

/// Message from client to server
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum ClientMessage {
    /// Send a chat message to the agent
    Chat { content: String },
    /// Request thread history
    GetHistory,
    /// Cancel ongoing agent operation
    Cancel,
    /// Ping to keep connection alive
    Ping,
}

/// Message from server to client
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum ServerMessage {
    /// Connection established
    Connected { session_id: String },
    /// Agent response text chunk
    TextChunk { content: String },
    /// Agent started using a tool
    ToolStart {
        tool_name: String,
        tool_input: serde_json::Value,
    },
    /// Tool execution result
    ToolResult {
        tool_name: String,
        result: String,
        error: Option<String>,
    },
    /// Agent finished responding
    ResponseComplete,
    /// Error occurred
    Error { message: String },
    /// Pong response
    Pong,
}

/// Handle a WebSocket connection
pub async fn handle_websocket(socket: WebSocket) {
    let (mut sender, mut receiver) = socket.split();

    // Generate session ID
    let session_id = uuid::Uuid::new_v4().to_string();
    info!("WebSocket connection established: {}", session_id);

    // Send connected message
    if let Err(e) = send_message(
        &mut sender,
        ServerMessage::Connected {
            session_id: session_id.clone(),
        },
    )
    .await
    {
        error!("Failed to send connected message: {:?}", e);
        return;
    }

    // Process incoming messages
    while let Some(msg) = receiver.next().await {
        match msg {
            Ok(Message::Text(text)) => {
                debug!("Received text message: {}", text);

                match serde_json::from_str::<ClientMessage>(&text) {
                    Ok(client_msg) => {
                        if let Err(e) = handle_client_message(client_msg, &mut sender).await {
                            error!("Error handling message: {:?}", e);
                            let _ = send_message(
                                &mut sender,
                                ServerMessage::Error {
                                    message: e.to_string(),
                                },
                            )
                            .await;
                        }
                    }
                    Err(e) => {
                        warn!("Failed to parse client message: {:?}", e);
                        let _ = send_message(
                            &mut sender,
                            ServerMessage::Error {
                                message: format!("Invalid message format: {}", e),
                            },
                        )
                        .await;
                    }
                }
            }
            Ok(Message::Binary(_)) => {
                debug!("Received binary message (ignoring)");
            }
            Ok(Message::Ping(_)) => {
                debug!("Received ping");
            }
            Ok(Message::Pong(_)) => {
                debug!("Received pong");
            }
            Ok(Message::Close(_)) => {
                info!("Client closed connection: {}", session_id);
                break;
            }
            Err(e) => {
                error!("WebSocket error: {:?}", e);
                break;
            }
        }
    }

    info!("WebSocket connection closed: {}", session_id);
}

/// Handle a client message
async fn handle_client_message(
    message: ClientMessage,
    sender: &mut futures::stream::SplitSink<WebSocket, Message>,
) -> Result<()> {
    match message {
        ClientMessage::Chat { content } => {
            handle_chat_message(content, sender).await?;
        }
        ClientMessage::GetHistory => {
            handle_get_history(sender).await?;
        }
        ClientMessage::Cancel => {
            // TODO: Implement cancellation
            send_message(
                sender,
                ServerMessage::Error {
                    message: "Cancellation not yet implemented".to_string(),
                },
            )
            .await?;
        }
        ClientMessage::Ping => {
            send_message(sender, ServerMessage::Pong).await?;
        }
    }
    Ok(())
}

/// Handle a chat message from the client
async fn handle_chat_message(
    content: String,
    sender: &mut futures::stream::SplitSink<WebSocket, Message>,
) -> Result<()> {
    info!("Processing chat message: {}", content);

    // For now, send a simple echo response
    // TODO: Integrate with actual agent system
    send_message(
        sender,
        ServerMessage::TextChunk {
            content: format!("Echo: {}", content),
        },
    )
    .await?;

    send_message(sender, ServerMessage::ResponseComplete).await?;

    Ok(())
}

/// Handle getting thread history
async fn handle_get_history(
    sender: &mut futures::stream::SplitSink<WebSocket, Message>,
) -> Result<()> {
    // TODO: Implement history retrieval
    send_message(
        sender,
        ServerMessage::Error {
            message: "History retrieval not yet implemented".to_string(),
        },
    )
    .await?;
    Ok(())
}

/// Helper to send a server message
async fn send_message(
    sender: &mut futures::stream::SplitSink<WebSocket, Message>,
    message: ServerMessage,
) -> Result<()> {
    let json = serde_json::to_string(&message)?;
    sender
        .send(Message::Text(json))
        .await
        .map_err(|e| anyhow!("Failed to send message: {}", e))
}
'''

def main():
    write_file("crates/agent_remote_server/src/server.rs", SERVER_RS)
    write_file("crates/agent_remote_server/src/websocket.rs", WEBSOCKET_RS)
    print("\n✅ All files created successfully!")

if __name__ == "__main__":
    main()
