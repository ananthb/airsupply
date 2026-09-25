module Main exposing (main)

{-| The airsupply page.

The whole page: state, view, and the HTTP it takes to keep them current. There
is no JavaScript, and nothing to keep in step with any.

Every request answers with the add-on's entire state rather than a patch of
it, so there is nothing here to merge and nothing that can go stale in one
corner while the rest moves on. Polling takes its pace from that state -- a
second apart while the machine is doing something, four while it is not --
which is a decision that belongs where the state is.

The page has one subject, the machine, and one job: say whether airsupply is
talking to it and show what it last read. Setting it up happens once, so it
takes up room only while it is unfinished.
-}

import Browser
import Html exposing (..)
import Html.Attributes exposing (..)
import Html.Events exposing (onClick, onInput, onSubmit)
import Http
import Json.Decode as D
import Json.Encode as E
import Time



-- MODEL


type Stage
    = NoAdapter
    | Choose
    | NeedsPairing
    | NeedsPin
    | Ready


type alias Machine =
    { address : String
    , name : String
    , signal : Maybe Int
    , classic : Bool
    , serial : Bool
    , bonded : Bool
    , candidate : Bool
    }


type alias Field =
    { label : String, value : String }


type alias Section =
    { title : String, ok : Bool, problem : Maybe String, fields : List Field }


type alias Reading =
    { at : String, sections : List Section }


type alias Question =
    { kind : String, code : Maybe String }


type alias Line =
    { at : String, level : String, text : String }


type alias State =
    { version : String
    , hasAdapter : Bool
    , stage : Stage
    , busy : Maybe String
    , scanning : Bool
    , machines : List Machine
    , machine : Maybe Machine
    , question : Maybe Question
    , problem : Maybe String
    , reading : Maybe Reading
    , log : List Line
    }


{-| What the page knows about the add-on.

Three states rather than the usual four. There is no "not asked": `init`
fires the first request, so the page is never sitting on a question it has
not put. And a failure after the first success keeps the state it already
had, because a poll that times out should not blank a reading somebody is
reading -- `Live state (Just err)` is where a page like this spends most of
any bad minute, and it is the state a plain
`NotAsked | Loading | Failure e | Success a` cannot hold.
-}
type Link
    = Starting
    | Unreachable Http.Error
    | Live State (Maybe Http.Error)


type alias Model =
    { link : Link
    , pin : String
    , reply : String
    , picking : Bool

    -- One request at a time. A tick that lands while the last one is still
    -- out is dropped rather than queued: the answer is the whole state, so
    -- the one already on its way says everything the second would have.
    , waiting : Bool
    }


init : () -> ( Model, Cmd Msg )
init _ =
    ( { link = Starting, pin = "", reply = "", picking = False, waiting = True }
    , fetch
    )



-- UPDATE


type Msg
    = Tick Time.Posix
    | Answered (Result Http.Error State)
    | Send String (List ( String, E.Value ))
    | PinTyped String
    | ReplyTyped String
    | SendPin
    | SendReply
    | TogglePicking


update : Msg -> Model -> ( Model, Cmd Msg )
update msg model =
    case msg of
        Tick _ ->
            if model.waiting then
                ( model, Cmd.none )

            else
                ( { model | waiting = True }, fetch )

        Answered result ->
            ( absorb result model, Cmd.none )

        Send kind extras ->
            ( { model | waiting = True }, act kind extras )

        PinTyped value ->
            ( { model | pin = digits 8 value }, Cmd.none )

        ReplyTyped value ->
            ( { model | reply = digits 8 value }, Cmd.none )

        SendPin ->
            ( { model | waiting = True }, act "pair" [ ( "pin", E.string model.pin ) ] )

        SendReply ->
            ( { model | waiting = True }
            , act "answer" [ ( "value", E.string model.reply ), ( "accept", E.bool True ) ]
            )

        TogglePicking ->
            ( { model | picking = not model.picking }, Cmd.none )


{-| Keep a typed code to digits. Both things a person types here -- the
machine's PIN and the Bluetooth code -- are numeric, and a stray space is a
failed handshake that looks like a wrong number.
-}
digits : Int -> String -> String
digits limit value =
    value |> String.filter Char.isDigit |> String.left limit


absorb : Result Http.Error State -> Model -> Model
absorb result model =
    case ( result, model.link ) of
        ( Err err, Live state _ ) ->
            -- Something has answered before. Keep it on screen and say only
            -- that the last try did not come back.
            { model | waiting = False, link = Live state (Just err) }

        ( Err err, _ ) ->
            { model | waiting = False, link = Unreachable err }

        ( Ok state, _ ) ->
            { model
                | link = Live state Nothing
                , waiting = False

                -- Clear a field once what it was for is over, so a code does
                -- not sit on screen after it has been accepted.
                , pin =
                    if state.stage == NeedsPin then
                        model.pin

                    else
                        ""
                , reply =
                    if state.question == Nothing then
                        ""

                    else
                        model.reply
                , picking =
                    if state.stage == Choose then
                        False

                    else
                        model.picking
            }


wrong : Http.Error -> String
wrong err =
    case err of
        Http.BadUrl url ->
            url ++ " is not a URL."

        Http.Timeout ->
            "It did not answer in time."

        Http.NetworkError ->
            "Nothing answered; it may have stopped."

        Http.BadStatus code ->
            "It answered " ++ String.fromInt code ++ "."

        Http.BadBody why ->
            "It answered something this page cannot read. " ++ why



-- THE ADD-ON


fetch : Cmd Msg
fetch =
    Http.get { url = "api/state", expect = expectState }


act : String -> List ( String, E.Value ) -> Cmd Msg
act kind extras =
    Http.post
        { url = "api/" ++ kind
        , body = Http.jsonBody (E.object extras)
        , expect = expectState
        }


{-| Decode the body whatever the status line says.

A refused action answers 4xx and puts the reason in the state it sends back,
which is the thing the page most needs at that moment --
`Http.expectJson` would discard it along with the rest of the body and leave
only the number. So the status is kept for the one case where it is all there
is: a body that is not state at all.
-}
expectState : Http.Expect Msg
expectState =
    Http.expectStringResponse Answered <|
        \response ->
            case response of
                Http.BadUrl_ url ->
                    Err (Http.BadUrl url)

                Http.Timeout_ ->
                    Err Http.Timeout

                Http.NetworkError_ ->
                    Err Http.NetworkError

                Http.BadStatus_ metadata body ->
                    decodeState body
                        |> Result.mapError (\_ -> Http.BadStatus metadata.statusCode)

                Http.GoodStatus_ _ body ->
                    decodeState body


decodeState : String -> Result Http.Error State
decodeState body =
    D.decodeString stateDecoder body
        |> Result.mapError (D.errorToString >> Http.BadBody)



-- DECODERS


stateDecoder : D.Decoder State
stateDecoder =
    let
        cons =
            D.map8 State
                (D.field "version" D.string)
                (D.field "has_adapter" D.bool)
                (D.field "stage" stageDecoder)
                (D.field "busy" (D.nullable D.string))
                (D.field "scanning" D.bool)
                (D.field "machines" (D.list machineDecoder))
                (D.field "machine" (D.nullable machineDecoder))
                (D.field "question" (D.nullable questionDecoder))
    in
    cons
        |> andMap (D.field "problem" (D.nullable D.string))
        |> andMap (D.field "reading" (D.nullable readingDecoder))
        |> andMap (D.field "log" (D.list lineDecoder))


andMap : D.Decoder a -> D.Decoder (a -> b) -> D.Decoder b
andMap =
    D.map2 (|>)


stageDecoder : D.Decoder Stage
stageDecoder =
    D.string
        |> D.map
            (\name ->
                case name of
                    "no-adapter" ->
                        NoAdapter

                    "choose" ->
                        Choose

                    "pairing" ->
                        NeedsPairing

                    "pin" ->
                        NeedsPin

                    _ ->
                        Ready
            )


machineDecoder : D.Decoder Machine
machineDecoder =
    D.map7 Machine
        (D.field "address" D.string)
        (D.field "name" D.string)
        (D.field "signal" (D.nullable D.int))
        (D.field "classic" D.bool)
        (D.field "serial" D.bool)
        (D.field "bonded" D.bool)
        (D.field "candidate" D.bool)


readingDecoder : D.Decoder Reading
readingDecoder =
    D.map2 Reading
        (D.field "at" D.string)
        (D.field "sections" (D.list sectionDecoder))


sectionDecoder : D.Decoder Section
sectionDecoder =
    D.map4 Section
        (D.field "title" D.string)
        (D.field "ok" D.bool)
        (D.field "problem" (D.nullable D.string))
        (D.field "fields" (D.list fieldDecoder))


fieldDecoder : D.Decoder Field
fieldDecoder =
    D.map2 Field (D.field "label" D.string) (D.field "value" D.string)


questionDecoder : D.Decoder Question
questionDecoder =
    D.map2 Question (D.field "kind" D.string) (D.field "code" (D.nullable D.string))


lineDecoder : D.Decoder Line
lineDecoder =
    D.map3 Line (D.field "at" D.string) (D.field "level" D.string) (D.field "text" D.string)



-- VIEW


view : Model -> Html Msg
view model =
    main_ []
        (case model.link of
            Starting ->
                [ p [ class "muted" ] [ text "Starting." ] ]

            Unreachable err ->
                [ div [ class "banner" ] [ text (notAnswering err) ] ]

            Live state lastTry ->
                page model state lastTry
        )


notAnswering : Http.Error -> String
notAnswering err =
    "The add-on is not answering. " ++ wrong err


page : Model -> State -> Maybe Http.Error -> List (Html Msg)
page model state lastTry =
    List.concat
        [ [ heading state ]
        , maybeView lastTry (\err -> div [ class "banner" ] [ text (notAnswering err) ])
        , maybeView state.problem (\why -> div [ class "banner" ] [ text why ])
        , maybeView state.question (question model state)
        , setup model state
        , readings state
        , [ activity state, footer state ]
        ]


maybeView : Maybe a -> (a -> Html Msg) -> List (Html Msg)
maybeView value render =
    case value of
        Just something ->
            [ render something ]

        Nothing ->
            []


heading : State -> Html Msg
heading state =
    div []
        [ div [ class "machine" ]
            (case state.machine of
                Just machine ->
                    [ h1 [] [ text (label machine) ], span [ class "addr" ] [ text machine.address ] ]

                Nothing ->
                    [ h1 [] [ text "No machine yet" ] ]
            )
        , div [ class "link" ]
            [ span [ class ("dot " ++ lampClass state) ] []
            , text (lampText state)
            ]
        ]


label : Machine -> String
label machine =
    if String.isEmpty machine.name then
        machine.address

    else
        machine.name


lampClass : State -> String
lampClass state =
    if state.busy /= Nothing || state.scanning then
        "work"

    else
        case state.stage of
            Ready ->
                "live"

            NoAdapter ->
                "none"

            _ ->
                "none"


lampText : State -> String
lampText state =
    case ( state.busy, state.scanning ) of
        ( Just "bond", _ ) ->
            "Pairing with Bluetooth."

        ( Just "pair", _ ) ->
            "Connecting to the machine."

        ( Just "read", _ ) ->
            "Reading."

        ( Just "forget", _ ) ->
            "Forgetting."

        ( Just other, _ ) ->
            other

        ( Nothing, True ) ->
            "Looking for machines."

        ( Nothing, False ) ->
            idleText state


idleText : State -> String
idleText state =
    case state.stage of
        NoAdapter ->
            "Home Assistant has no Bluetooth adapter."

        Choose ->
            "Not set up yet."

        NeedsPairing ->
            "Not paired with Bluetooth."

        NeedsPin ->
            "Paired. Waiting for the machine's PIN."

        Ready ->
            case state.reading of
                Just reading ->
                    "Connected. Read at " ++ reading.at ++ "."

                Nothing ->
                    "Connected. Nothing read yet."



-- SETUP


setup : Model -> State -> List (Html Msg)
setup model state =
    let
        working =
            state.busy /= Nothing
    in
    case state.stage of
        NoAdapter ->
            [ div [ class "card" ]
                [ p [ class "note" ]
                    [ text "Home Assistant cannot see a Bluetooth adapter, so nothing here can work. Check that the host has one and that the Bluetooth integration is running." ]
                ]
            ]

        Choose ->
            [ div [ class "card" ]
                [ p [ class "note" ] [ text "Put the AirMini in pairing mode, then look for it. It is only discoverable while it is." ]
                , div [ class "row-btns" ] [ scanButton state ]
                , machineTable state
                ]
            ]

        NeedsPairing ->
            [ div [ class "card" ]
                [ p [ class "note" ] [ text "Put the AirMini in pairing mode and pair within a few seconds of it lighting up. It only answers while it is in pairing mode." ]
                , div [ class "row-btns" ]
                    [ button [ class "go", onClick (Send "bond" []), disabled working ] [ text "Pair" ]
                    , scanButton state
                    , forgetButton working
                    ]
                ]
            ]

        NeedsPin ->
            [ div [ class "card" ]
                [ p [ class "note" ] [ text "Type the PIN on the machine's screen. Once only: airsupply keeps the key the machine gives back, and connects with that from then on." ]
                , Html.form [ class "row-btns", onSubmit SendPin ]
                    [ input
                        [ value model.pin
                        , onInput PinTyped
                        , inputmode "numeric"
                        , autocomplete False
                        , attribute "autofocus" ""
                        , placeholder "PIN"
                        , attribute "aria-label" "PIN on the machine's screen"
                        ]
                        []
                    , button [ class "go", disabled (working || String.isEmpty model.pin) ] [ text "Connect" ]
                    , forgetButton working
                    ]
                ]
            ]

        Ready ->
            [ div [ class "row-btns", style "margin-bottom" "16px" ]
                [ button [ class "go", onClick (Send "read" []), disabled working ] [ text "Read now" ]
                , button [ class "plain", onClick TogglePicking ]
                    [ text
                        (if model.picking then
                            "Close"

                         else
                            "Change machine"
                        )
                    ]
                ]
            , if model.picking then
                div [ class "card" ]
                    [ div [ class "row-btns" ] [ scanButton state, forgetButton working ]
                    , machineTable state
                    ]

              else
                text ""
            ]


scanButton : State -> Html Msg
scanButton state =
    button
        [ onClick (Send "scan" []), disabled (state.scanning || state.busy /= Nothing || not state.hasAdapter) ]
        [ text
            (if state.scanning then
                "Looking"

             else
                "Look for machines"
            )
        ]


forgetButton : Bool -> Html Msg
forgetButton working =
    button [ class "plain", onClick (Send "forget" []), disabled working ] [ text "Forget" ]


machineTable : State -> Html Msg
machineTable state =
    let
        candidates =
            List.filter .candidate state.machines

        others =
            List.filter (not << .candidate) state.machines
    in
    div []
        [ if List.isEmpty candidates then
            p [ class "note", style "margin-top" "12px" ]
                [ text "No AirMini found yet. If it is in pairing mode and still does not appear, Home Assistant is out of range." ]

          else
            table [] (List.map (machineRow state) candidates)
        , if List.isEmpty others then
            text ""

          else
            details []
                [ summary [] [ text (String.fromInt (List.length others) ++ " other Bluetooth devices") ]
                , table [] (List.map (machineRow state) others)
                ]
        ]


machineRow : State -> Machine -> Html Msg
machineRow state machine =
    let
        chosen =
            Just machine.address == Maybe.map .address state.machine
    in
    tr []
        [ td []
            [ div [ class "name" ] [ text (label machine) ]
            , div [ class "sub" ] [ text (machine.address ++ describe machine) ]
            ]
        , td [ class "sig" ] [ text (signalText machine) ]
        , td [ class "act" ]
            [ if chosen then
                span [ class "sub" ] [ text "in use" ]

              else
                button [ onClick (Send "select" [ ( "address", E.string machine.address ) ]) ] [ text "Use" ]
            ]
        ]


describe : Machine -> String
describe machine =
    String.concat
        [ if machine.classic then
            ""

          else
            "  not Bluetooth Classic"
        , if machine.bonded then
            "  paired"

          else
            ""
        ]


signalText : Machine -> String
signalText machine =
    case machine.signal of
        Just dbm ->
            String.fromInt dbm
                ++ " dBm"
                ++ (if dbm < -80 then
                        " (weak)"

                    else
                        ""
                   )

        Nothing ->
            "not in range"



-- THE PAIRING QUESTION


question : Model -> State -> Question -> Html Msg
question model state ask =
    let
        who =
            Maybe.map label state.machine |> Maybe.withDefault "the machine"

        code =
            Maybe.withDefault "" ask.code

        cancel =
            button [ onClick (Send "answer" [ ( "accept", E.bool False ) ]) ] [ text "Cancel" ]

    in
    div [ class "card ask" ]
        (case ask.kind of
            "pincode" ->
                [ p [] [ text ("Bluetooth PIN for " ++ who ++ ". If the machine shows one, type it; otherwise try 0000.") ]
                , Html.form [ class "row-btns", onSubmit SendReply ]
                    [ input [ value model.reply, onInput ReplyTyped, inputmode "numeric", autocomplete False, attribute "autofocus" "", attribute "aria-label" "Bluetooth PIN" ] []
                    , button [ class "go" ] [ text "Send" ]
                    , cancel
                    ]
                ]

            "passkey" ->
                [ p [] [ text ("Type the six-digit code " ++ who ++ " is showing.") ]
                , Html.form [ class "row-btns", onSubmit SendReply ]
                    [ input [ value model.reply, onInput ReplyTyped, inputmode "numeric", autocomplete False, attribute "autofocus" "", attribute "aria-label" "Six-digit code" ] []
                    , button [ class "go" ] [ text "Send" ]
                    , cancel
                    ]
                ]

            "confirm" ->
                [ p [] [ text ("Does " ++ who ++ " show this code?") ]
                , p [ class "code" ] [ text code ]
                , div [ class "row-btns" ]
                    [ button [ class "go", onClick (Send "answer" [ ( "accept", E.bool True ) ]) ] [ text "It matches" ]
                    , button [ onClick (Send "answer" [ ( "accept", E.bool False ) ]) ] [ text "It does not" ]
                    ]
                ]

            "authorize" ->
                [ p [] [ text (who ++ " wants to pair.") ]
                , div [ class "row-btns" ]
                    [ button [ class "go", onClick (Send "answer" [ ( "accept", E.bool True ) ]) ] [ text "Allow" ]
                    , button [ onClick (Send "answer" [ ( "accept", E.bool False ) ]) ] [ text "Deny" ]
                    ]
                ]

            _ ->
                [ p [] [ text ("Enter this code on " ++ who ++ ".") ]
                , p [ class "code" ] [ text code ]
                ]
        )



-- READINGS


readings : State -> List (Html Msg)
readings state =
    case state.reading of
        Nothing ->
            []

        Just reading ->
            [ div [ class "card readings" ] (List.map section reading.sections) ]


section : Section -> Html Msg
section content =
    div [ class "section" ]
        [ h2 [] [ text content.title ]
        , case ( content.problem, content.fields ) of
            ( Just why, _ ) ->
                p [ class "failed" ] [ text why ]

            ( Nothing, [] ) ->
                p [ class "muted" ] [ text "Nothing returned." ]

            ( Nothing, fields ) ->
                dl [] (List.map field fields)
        ]


field : Field -> Html Msg
field one =
    div [ class "row" ]
        [ dt [] [ text one.label ]
        , dd [] [ text one.value ]
        ]



-- ACTIVITY


activity : State -> Html Msg
activity state =
    details []
        [ summary [] [ text "Activity" ]
        , pre [ class "log" ] (List.map line state.log)
        ]


line : Line -> Html Msg
line one =
    span [ class one.level ] [ text (one.at ++ "  " ++ one.text ++ "\n") ]


footer : State -> Html Msg
footer state =
    p [ class "foot" ] [ text ("airsupply " ++ state.version ++ ". Reads only; it never writes to the machine.") ]



-- WIRING


inputmode : String -> Attribute msg
inputmode =
    attribute "inputmode"


{-| How often to ask again.

A second apart while the machine is doing something or waiting to be answered,
four while it is idle. The page already knows which, so the pace is read off
the state rather than guessed at from outside it.
-}
pace : Model -> Float
pace model =
    case model.link of
        Live state _ ->
            if state.busy /= Nothing || state.scanning || state.question /= Nothing then
                1000

            else
                4000

        _ ->
            1000


main : Program () Model Msg
main =
    Browser.element
        { init = init
        , update = update
        , view = view
        , subscriptions = \model -> Time.every (pace model) Tick
        }
